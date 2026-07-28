import os
import git
import secrets
import shutil
import sqlite3
import requests
import time
import hashlib
from collections import defaultdict, deque
from datetime import date, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from flask import Flask, jsonify, render_template, url_for, redirect, request, session
from flask_behind_proxy import FlaskBehindProxy
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field
from backend.services.wellness import (
    delete_saved,
    generate_cosmetic_remedies,
    generate_drug_comparison,
    generate_drug_remedies,
    generate_food_remedies,
    get_link,
    get_recipe_details,
    search_recipes,
    select_item,
    show_db,
)
from backend.services.nutrition import (
    food_log_values,
    get_usda_food,
    search_usda_foods,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = PROJECT_ROOT / 'frontend'
DATABASE_ROOT = PROJECT_ROOT / 'database'
RUNTIME_ROOT = PROJECT_ROOT / 'instance'

app = Flask(
    __name__,
    template_folder=str(FRONTEND_ROOT / 'templates'),
    static_folder=str(FRONTEND_ROOT / 'static'),
    static_url_path='/static',
)
proxied = FlaskBehindProxy(app)
DATABASE = DATABASE_ROOT / 'ruby.db'
LEGACY_DATABASE = RUNTIME_ROOT / 'ruby.db'
SECRET_FILE = RUNTIME_ROOT / 'secret_key'
SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_PUBLISHABLE_KEY = os.environ.get('SUPABASE_PUBLISHABLE_KEY', '')
GOOGLE_CLIENT_ID = os.environ.get(
    'GOOGLE_CLIENT_ID',
    '725724612296-hc0jlt97b7a1bs5svmobbdo7o6p101sv.apps.googleusercontent.com',
)
LOGIN_ATTEMPTS = defaultdict(deque)
FOOD_SEARCH_ATTEMPTS = defaultdict(deque)

DATABASE_ROOT.mkdir(parents=True, exist_ok=True)
if not DATABASE.exists() and LEGACY_DATABASE.exists():
    shutil.copy2(LEGACY_DATABASE, DATABASE)

class RubyGuidance(BaseModel):
    is_wellness_related: bool = Field(
        description='True only when the user is asking about health, wellness, nutrition, fitness, sleep, skincare, medication safety, or personal care.'
    )
    answer: str = Field(description='Concise educational wellness guidance in plain text.')
    ranked_categories: list[Literal['food', 'drugs', 'cosmetics']] = Field(
        min_length=3,
        max_length=3,
        description='Food, drugs, and cosmetics ranked from most to least relevant, each exactly once.'
    )

class RoutineStep(BaseModel):
    product: str = Field(description='Product or step name, in the order it should be used.')
    reason: str = Field(description='One short sentence on why this step is included or how to use it.')
    tip: str = Field(description='One short practical tip for this specific step, e.g. patch testing or application technique.')

class RubyRoutine(BaseModel):
    introduction: str = Field(description='1-2 sentence general overview of how this product fits into a routine.')
    product_type: str = Field(description='The likely product category, e.g. Moisturizer, Cleanser, Shampoo, Serum, Sunscreen.')
    routine_steps: list[RoutineStep] = Field(description='Ordered routine steps. Reference product categories, use specific brand names if possible for more descriptive tailored routine.')
    frequency: str = Field(description='When and how often to use it, e.g. "morning" or "as needed, 2-3x per week".')
    steps: list[str] = Field(description='Short practical application tips or prep steps relevant to the product category.')
    warnings: list[str] = Field(description='General product cautions, e.g., stop use if irritation or discomfort occurs, consult a professional for persistent symptoms')

def get_secret_key():
    environment_key = os.environ.get('SECRET_KEY')
    if environment_key:
        return environment_key
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(32))
    SECRET_FILE.chmod(0o600)
    return SECRET_FILE.read_text().strip()

app.config['SECRET_KEY'] = get_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('FLASK_ENV') == 'production',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)

def client_ip():
    forwarded = request.headers.get('X-Forwarded-For', '')
    return forwarded.split(',', 1)[0].strip() or request.remote_addr or 'unknown'

def login_rate_limited():
    now = time.monotonic()
    attempts = LOGIN_ATTEMPTS[client_ip()]
    while attempts and attempts[0] < now - 900:
        attempts.popleft()
    if len(attempts) >= 8:
        return True
    attempts.append(now)
    return False

def food_search_rate_limited():
    now = time.monotonic()
    attempts = FOOD_SEARCH_ATTEMPTS[client_ip()]
    while attempts and attempts[0] < now - 60:
        attempts.popleft()
    if len(attempts) >= 30:
        return True
    attempts.append(now)
    return False

def auth_form_token():
    if 'auth_form_token' not in session:
        session['auth_form_token'] = secrets.token_urlsafe(32)
    return session['auth_form_token']

def valid_auth_form():
    expected = session.get('auth_form_token', '')
    supplied = request.form.get('auth_form_token', '')
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))

def supabase_auth(path, payload):
    if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY:
        raise RuntimeError('Supabase authentication is not configured.')
    return requests.post(
        f'{SUPABASE_URL}/auth/v1/{path}',
        headers={
            'apikey': SUPABASE_PUBLISHABLE_KEY,
            'Content-Type': 'application/json',
        },
        json=payload,
        timeout=10,
    )

def establish_session(user):
    metadata = user.get('user_metadata') or {}
    display_name = (
        metadata.get('full_name')
        or metadata.get('name')
        or metadata.get('display_name')
        or user.get('email', '').split('@', 1)[0]
    )
    avatar_url = metadata.get('avatar_url', '')
    if not avatar_url.startswith('https://lh3.googleusercontent.com/'):
        avatar_url = None
    session.clear()
    session.permanent = True
    session['user_id'] = user.get('id')
    session['username'] = display_name
    session['user_email'] = user.get('email')
    session['avatar_url'] = avatar_url

@app.context_processor
def google_sign_in_context():
    if not GOOGLE_CLIENT_ID or session.get('user_id'):
        return {'google_client_id': None, 'google_nonce_hash': None}
    nonce = secrets.token_urlsafe(32)
    session['google_sign_in_nonce'] = nonce
    return {
        'google_client_id': GOOGLE_CLIENT_ID,
        'google_nonce_hash': hashlib.sha256(nonce.encode()).hexdigest(),
    }

@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Cache-Control'] = 'no-store'
    return response

@app.template_filter('format_price')
def format_price(value):
    if value in (None, '', 'N/A', 'Price not available'):
        return 'Price unavailable'
    try:
        amount = float(str(value).replace('$', '').strip())
    except (TypeError, ValueError):
        return str(value)
    if amount <= 0:
        return 'Price unavailable'
    return f'${amount:.2f}'

def get_database():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection

def init_database():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    with get_database() as connection:
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                description TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS ruby_responses (
                user_id TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                ranking TEXT NOT NULL DEFAULT '["food", "drugs", "cosmetics"]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        

            CREATE TABLE IF NOT EXISTS routines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                introduction TEXT NOT NULL,
                product_type TEXT NOT NULL,
                frequency TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS routine_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                routine_id INTEGER NOT NULL,
                product TEXT NOT NULL,
                reason TEXT NOT NULL,
                tip TEXT NOT NULL,
                FOREIGN KEY (routine_id) REFERENCES routines(id)
            );

            CREATE TABLE IF NOT EXISTS routine_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                routine_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                type TEXT NOT NULL,
                FOREIGN KEY (routine_id) REFERENCES routines(id)
            );

            CREATE TABLE IF NOT EXISTS saved_recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                spoonacular_id INTEGER NOT NULL,
                title TEXT NOT NULL, 
                image TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, spoonacular_id)
            );

            CREATE TABLE IF NOT EXISTS nutrition_profiles (
                user_id TEXT PRIMARY KEY,
                age INTEGER NOT NULL,
                estimate_sex TEXT NOT NULL,
                height_cm REAL NOT NULL,
                weight_kg REAL NOT NULL,
                activity_level TEXT NOT NULL,
                estimated_calories INTEGER NOT NULL,
                manual_calorie_target INTEGER,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS food_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                saved_recipe_id INTEGER,
                fdc_id INTEGER,
                title TEXT NOT NULL,
                amount_grams REAL,
                calories REAL NOT NULL DEFAULT 0,
                protein_g REAL NOT NULL DEFAULT 0,
                carbs_g REAL NOT NULL DEFAULT 0,
                fat_g REAL NOT NULL DEFAULT 0,
                fiber_g REAL NOT NULL DEFAULT 0,
                calcium_mg REAL NOT NULL DEFAULT 0,
                iron_mg REAL NOT NULL DEFAULT 0,
                potassium_mg REAL NOT NULL DEFAULT 0,
                vitamin_c_mg REAL NOT NULL DEFAULT 0,
                vitamin_d_mcg REAL NOT NULL DEFAULT 0,
                log_date TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (saved_recipe_id) REFERENCES saved_recipes(id)
            );

        ''')

        response_columns = {
            row['name'] for row in connection.execute('PRAGMA table_info(ruby_responses)')
        }
        if 'ranking' not in response_columns:
            connection.execute(
                '''
                ALTER TABLE ruby_responses
                ADD COLUMN ranking TEXT NOT NULL DEFAULT '["food", "drugs", "cosmetics"]'
                '''
            )

        food_log_columns = {
            row['name'] for row in connection.execute('PRAGMA table_info(food_log)')
        }
        if 'fdc_id' not in food_log_columns:
            connection.execute('ALTER TABLE food_log ADD COLUMN fdc_id INTEGER')
        if 'amount_grams' not in food_log_columns:
            connection.execute('ALTER TABLE food_log ADD COLUMN amount_grams REAL')

def get_user_id():
    if 'user_id' not in session:
        session['user_id'] = uuid4().hex
    return session['user_id']

def add_activity(connection, user_id, description):
    connection.execute(
        'INSERT INTO activities (user_id, description) VALUES (?, ?)',
        (user_id, description)
    )

ACTIVITY_MULTIPLIERS = {
    'sedentary': 1.2,
    'light': 1.375,
    'moderate': 1.55,
    'very_active': 1.725,
}

def estimate_maintenance_calories(age, estimate_sex, height_cm, weight_kg, activity_level):
    sex_adjustment = 5 if estimate_sex == 'male' else -161
    resting_energy = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) + sex_adjustment
    return max(1, round(resting_energy * ACTIVITY_MULTIPLIERS[activity_level]))

def recipe_nutrients(recipe):
    nutrients = {
        str(item.get('name', '')).lower(): item
        for item in (recipe.get('nutrition') or {}).get('nutrients', [])
    }

    def amount(name):
        try:
            return max(0.0, float(nutrients.get(name.lower(), {}).get('amount', 0)))
        except (TypeError, ValueError):
            return 0.0

    return {
        'calories': amount('Calories'),
        'protein_g': amount('Protein'),
        'carbs_g': amount('Carbohydrates'),
        'fat_g': amount('Fat'),
        'fiber_g': amount('Fiber'),
        'calcium_mg': amount('Calcium'),
        'iron_mg': amount('Iron'),
        'potassium_mg': amount('Potassium'),
        'vitamin_c_mg': amount('Vitamin C'),
        'vitamin_d_mcg': amount('Vitamin D'),
    }

init_database()

def get_dashboard_context():
    user_id = get_user_id()
    with get_database() as connection:
        tasks = connection.execute(
            'SELECT * FROM tasks WHERE user_id = ? ORDER BY completed, id DESC',
            (user_id,)
        ).fetchall()
        connection.execute(
            'DELETE FROM ruby_responses WHERE user_id = ?',
            (user_id,)
        )
        nutrition_profile = connection.execute(
            'SELECT * FROM nutrition_profiles WHERE user_id = ?',
            (user_id,),
        ).fetchone()
        saved_recipes = connection.execute(
            'SELECT * FROM saved_recipes WHERE user_id = ? ORDER BY id DESC',
            (user_id,),
        ).fetchall()
        food_log = connection.execute(
            '''
            SELECT * FROM food_log
            WHERE user_id = ? AND log_date = ?
            ORDER BY id DESC
            ''',
            (user_id, date.today().isoformat()),
        ).fetchall()
    ruby_response = session.pop('ruby_response', None)
    completed_count = sum(task['completed'] for task in tasks)
    nutrition_totals = {
        key: round(sum(float(item[key]) for item in food_log), 1)
        for key in (
            'calories', 'protein_g', 'carbs_g', 'fat_g', 'fiber_g',
            'calcium_mg', 'iron_mg', 'potassium_mg',
            'vitamin_c_mg', 'vitamin_d_mcg',
        )
    }
    calorie_target = 0
    if nutrition_profile:
        calorie_target = (
            nutrition_profile['manual_calorie_target']
            or nutrition_profile['estimated_calories']
        )
    calorie_progress = (
        min(100, round(nutrition_totals['calories'] / calorie_target * 100))
        if calorie_target else 0
    )
    context = {
        'username': session.get('username'),
        'tasks': tasks,
        'completed_count': completed_count,
        'nutrition_profile': nutrition_profile,
        'nutrition_totals': nutrition_totals,
        'calorie_target': calorie_target,
        'calorie_progress': calorie_progress,
        'saved_recipes': saved_recipes,
        'food_log': food_log,
        'auth_form_token': auth_form_token(),
        'dashboard_notice': session.pop('dashboard_notice', None),
        'dashboard_error': session.pop('dashboard_error', None),
    }
    if ruby_response:
        context.update(ruby_response)
    ruby_error = session.pop('ruby_error', None)
    error_question = session.pop('ruby_error_question', None)
    if ruby_error:
        context['ruby_error'] = ruby_error
        context['ruby_question'] = error_question
        context.pop('ruby_answer', None)


    return context

def render_dashboard(**extra_context):
    context = get_dashboard_context()
    context.update(extra_context)
    return render_template('welcome.html', **context)

def generate_ruby_answer(client, model, question):
    response = client.models.generate_content(
        model= model,
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=(
                'You are Ruby, a concise wellness information assistant. '
                'First decide whether the request is directly related to health, wellness, nutrition, fitness, sleep, skincare, medication safety, or personal care. '
                'Requests about literature, schoolwork, coding, entertainment, politics, general knowledge, or other unrelated subjects are not wellness-related. '
                'Do not answer, summarize, or creatively reinterpret unrelated requests as wellness topics. '
                'For every unrelated request, set is_wellness_related to false and set answer exactly to: Ruby can only help with wellness-related questions. '
                'For a related request, set is_wellness_related to true and answer normally. '
                'Give general educational guidance in plain language. '
                'Do not diagnose conditions, prescribe treatment, or recommend changing medication dosages. '
                'Encourage professional medical care when symptoms may require it. '
                'For possible emergencies, tell the user to contact local emergency services immediately. '
                'Use plain text without Markdown and keep responses under 180 words. '
                'Rank food, drugs, and cosmetics by which information section is most relevant to explore next. '
                'Food means nutrition and recipes, drugs means medication information, and cosmetics means skincare or personal care. '
                'The ranking is navigation guidance, not a recommendation to take medication or buy a product.'
            ),
            thinking_config=types.ThinkingConfig(thinking_level='minimal'),
            max_output_tokens=800,
            response_mime_type='application/json',
            response_schema=RubyGuidance
        )
    )
    return RubyGuidance.model_validate_json(response.text)

def generate_routine_answer(client, model, product_name):
    response = client.models.generate_content(
        model=model,
        contents=product_name,
        config=types.GenerateContentConfig(
            system_instruction=(
                "You are Ruby, a wellness product routine assistant. "
                "The user will provide the name of a skincare, haircare, cosmetic, or personal care product. "
                "Explain how that product can generally fit into a safe, practical routine. "
                "Identify the likely product type, such as moisturizer, cleanser, shampoo, conditioner, serum, or sunscreen. "
                "Provide the routine in a clear order and explain when the product should be used, such as morning, evening, wash day, or as needed. "
                "Include general frequency guidance when appropriate. "
                "When referencing other products in the routine, use general product categories such as 'a gentle cleanser' or 'a leave-in conditioner' rather than specific brand names, since you cannot verify what specific products exist or what they contain. "
                "Do not invent exact ingredients, benefits, warnings, or directions that are not confirmed by the product name. "
                "If the product name is vague or could refer to multiple products, clearly state that the guidance is general and tell the user to check the product label for exact directions. "
                "Do not diagnose conditions, prescribe treatment, or claim that the product will cure a medical issue. "
                "Recommend patch testing for new skincare or cosmetic products and stopping use if irritation occurs. "
                "Encourage the user to consult a healthcare professional for severe, persistent, or worsening symptoms. "
                "Use plain text without Markdown. Keep each field concise and practical."
            ),
            thinking_config=types.ThinkingConfig(thinking_level='minimal'),
            max_output_tokens=800,
            response_mime_type = 'application/json',
            response_schema = RubyRoutine
        )
    )
    return RubyRoutine.model_validate_json(response.text)



@app.post("/update_server")
def webhook():
    deploy_secret = os.environ.get('DEPLOY_WEBHOOK_SECRET', '')
    supplied_secret = request.headers.get('Authorization', '').removeprefix('Bearer ')
    if not deploy_secret or not secrets.compare_digest(deploy_secret, supplied_secret):
        return 'Not found', 404

    repo_path = Path(os.environ.get('DEPLOY_REPO_PATH', '/home/rubywellness/Ruby'))
    wsgi_path = Path(os.environ.get(
        'PYTHONANYWHERE_WSGI_FILE',
        '/var/www/rubywellness_pythonanywhere_com_wsgi.py',
    ))

    try:
        repo = git.Repo(repo_path)
        if repo.is_dirty(untracked_files=False):
            app.logger.error('Deployment stopped because the server repository has local changes')
            return 'Server repository has local changes', 409

        origin = repo.remotes.origin
        origin.fetch('main')

        if 'main' not in repo.heads:
            main_branch = repo.create_head('main', origin.refs.main)
            main_branch.set_tracking_branch(origin.refs.main)
        repo.heads.main.checkout()
        origin.pull('main', ff_only=True)
        wsgi_path.touch()
    except Exception:
        app.logger.exception('Automated deployment failed')
        return 'Deployment failed', 500

    return f'Deployed {repo.head.commit.hexsha[:7]}', 200

@app.route('/')
def home():
    if not session.get('username'):
        return redirect(url_for('login'))
    return render_dashboard()

@app.route('/login')
def login():
    if session.get('user_id') and session.get('username'):
        return redirect(url_for('home'))
    return render_template(
        'login.html',
        register=request.args.get('mode') == 'register',
        auth_form_token=auth_form_token(),
    )

@app.post('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/entry', methods= ["GET", "POST"])
def entry():
    if request.method != 'POST':
        return redirect(url_for('login'))
    if not valid_auth_form():
        return redirect(url_for('login'))
    if login_rate_limited():
        return render_template(
            'login.html',
            error='Too many attempts. Please wait 15 minutes and try again.',
            auth_form_token=auth_form_token(),
        ), 429

    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    try:
        response = supabase_auth(
            'token?grant_type=password',
            {'email': email, 'password': password},
        )
    except (RuntimeError, requests.RequestException):
        return render_template(
            'login.html',
            error='Login is temporarily unavailable. Please try again.',
            auth_form_token=auth_form_token(),
        ), 503

    if not response.ok:
        return render_template(
            'login.html',
            error='Invalid email or password.',
            auth_form_token=auth_form_token(),
        ), 401

    user = response.json().get('user', {})
    establish_session(user)
    if not session.get('username'):
        session['username'] = email
    LOGIN_ATTEMPTS.pop(client_ip(), None)
    return redirect(url_for('home'))

@app.post('/auth/google/token')
def google_token():
    nonce = session.pop('google_sign_in_nonce', '')
    credential = request.form.get('credential', '')
    if not valid_auth_form() or not nonce or not credential:
        return render_template(
            'login.html',
            error='Google sign-in could not be completed. Please try again.',
            auth_form_token=auth_form_token(),
        ), 400
    if login_rate_limited():
        return render_template(
            'login.html',
            error='Too many attempts. Please wait 15 minutes and try again.',
            auth_form_token=auth_form_token(),
        ), 429
    try:
        response = supabase_auth(
            'token?grant_type=id_token',
            {'provider': 'google', 'id_token': credential, 'nonce': nonce},
        )
    except (RuntimeError, requests.RequestException):
        response = None
    if response is None or not response.ok:
        return render_template(
            'login.html',
            error='Google sign-in could not be completed. Please try again.',
            auth_form_token=auth_form_token(),
        ), 401

    user = response.json().get('user', {})
    if not user.get('id') or not user.get('email'):
        return render_template(
            'login.html',
            error='Google did not provide a usable account.',
            auth_form_token=auth_form_token(),
        ), 401
    establish_session(user)
    LOGIN_ATTEMPTS.pop(client_ip(), None)
    return redirect(url_for('home'))

@app.post('/register')
def register():
    if not valid_auth_form():
        return redirect(url_for('login', mode='register'))
    if login_rate_limited():
        return render_template(
            'login.html',
            register=True,
            error='Too many attempts. Please wait 15 minutes and try again.',
            auth_form_token=auth_form_token(),
        ), 429

    full_name = request.form.get('full_name', '').strip()
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    if not full_name or len(full_name) > 80:
        return render_template(
            'login.html',
            register=True,
            error='Enter your name.',
            auth_form_token=auth_form_token(),
        ), 400
    if len(password) < 12:
        return render_template(
            'login.html',
            register=True,
            error='Use a password with at least 12 characters.',
            auth_form_token=auth_form_token(),
        ), 400
    try:
        response = supabase_auth(
            'signup',
            {
                'email': email,
                'password': password,
                'data': {'full_name': full_name},
            },
        )
    except (RuntimeError, requests.RequestException):
        return render_template(
            'login.html',
            register=True,
            error='Registration is temporarily unavailable. Please try again.',
            auth_form_token=auth_form_token(),
        ), 503

    if not response.ok:
        return render_template(
            'login.html',
            register=True,
            error='Registration could not be completed. Try a different email.',
            auth_form_token=auth_form_token(),
        ), 400
    LOGIN_ATTEMPTS.pop(client_ip(), None)
    return render_template(
        'login.html',
        message='Check your email to confirm your account, then log in.',
        auth_form_token=auth_form_token(),
    )

@app.post('/ask-ruby')
def ask_ruby():
    question = request.form.get('question', '').strip()[:1000]
    if not question:
        return redirect(url_for('home'))

    api_key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GENAI_KEY')
    if not api_key:
        session['ruby_error'] = 'Gemini is not configured. Set GEMINI_API_KEY and try again.'
        session['ruby_error_question'] = question
        return redirect(url_for('home'))

    try:
        client = genai.Client(api_key=api_key)
        primary_model = os.environ.get('GEMINI_MODEL', 'gemini-3.1-flash-lite')
        fallback_model = os.environ.get('GEMINI_FALLBACK_MODEL', 'gemini-3.5-flash')
        try:
            guidance = generate_ruby_answer(client, primary_model, question)
        except errors.ServerError as error:
            if error.code != 503 or fallback_model == primary_model:
                raise
            app.logger.warning('Gemini primary model unavailable; using fallback model')
            guidance = generate_ruby_answer(client, fallback_model, question)
        if guidance.is_wellness_related:
            ranking = list(dict.fromkeys(guidance.ranked_categories))
            ranking.extend(category for category in ('food', 'drugs', 'cosmetics') if category not in ranking)
            answer = guidance.answer or 'Ruby could not generate a response. Please try again.'
            top_category = ranking[0]
            if top_category == 'food':
                options = generate_food_remedies(question)
            elif top_category == 'drugs':
                options = generate_drug_remedies(question)
            else:
                options = generate_cosmetic_remedies(question)
        else:
            ranking = []
            options = []
            top_category = None
            answer = 'Ruby can only help with wellness-related questions.'
        session['ruby_response'] = {
            'ruby_question': question,
            'ruby_answer': answer,
            'ruby_ranking': ranking,
            'ruby_is_wellness_related': guidance.is_wellness_related,
            'ruby_options': options,
            'ruby_category': top_category
        }
        return redirect(url_for('home'))
    except errors.ServerError as error:
        if error.code == 503:
            session['ruby_error'] = 'Gemini is temporarily busy. Please wait a moment and try again.'
            session['ruby_error_question'] = question
            return redirect(url_for('home'))
        app.logger.exception('Gemini server request failed')
        session['ruby_error'] = 'Ruby could not answer right now. Please try again shortly.'
        session['ruby_error_question'] = question
        return redirect(url_for('home'))
    except Exception:
        app.logger.exception('Gemini request failed')
        session['ruby_error'] = 'Ruby could not answer right now. Please try again shortly.'
        session['ruby_error_question'] = question
        return redirect(url_for('home'))



@app.post('/tasks')
def add_task():
    title = request.form.get('title', '').strip()[:120]
    if title:
        user_id = get_user_id()
        with get_database() as connection:
            connection.execute(
                'INSERT INTO tasks (user_id, title) VALUES (?, ?)',
                (user_id, title)
            )
            add_activity(connection, user_id, f'Added task: {title}')
    return redirect(url_for('home'))

@app.post('/tasks/<int:task_id>/toggle')
def toggle_task(task_id):
    user_id = get_user_id()
    with get_database() as connection:
        task = connection.execute(
            'SELECT title, completed FROM tasks WHERE id = ? AND user_id = ?',
            (task_id, user_id)
        ).fetchone()
        if task:
            completed = 0 if task['completed'] else 1
            connection.execute(
                'UPDATE tasks SET completed = ? WHERE id = ? AND user_id = ?',
                (completed, task_id, user_id)
            )
            action = 'Completed' if completed else 'Reopened'
            add_activity(connection, user_id, f'{action}: {task["title"]}')
    return redirect(url_for('home'))

@app.post('/tasks/<int:task_id>/edit')
def edit_task(task_id):
    title = request.form.get('title', '').strip()[:120]
    if title:
        user_id = get_user_id()
        with get_database() as connection:
            updated = connection.execute(
                'UPDATE tasks SET title = ? WHERE id = ? AND user_id = ?',
                (title, task_id, user_id)
            )
            if updated.rowcount:
                add_activity(connection, user_id, f'Updated task: {title}')
    return redirect(url_for('home'))

@app.post('/tasks/<int:task_id>/delete')
def delete_task(task_id):
    user_id = get_user_id()
    with get_database() as connection:
        task = connection.execute(
            'SELECT title FROM tasks WHERE id = ? AND user_id = ?',
            (task_id, user_id)
        ).fetchone()
        if task:
            connection.execute(
                'DELETE FROM tasks WHERE id = ? AND user_id = ?',
                (task_id, user_id)
            )
            add_activity(connection, user_id, f'Deleted task: {task["title"]}')
    return redirect(url_for('home'))

@app.post('/nutrition/profile')
def save_nutrition_profile():
    if not session.get('username') or not valid_auth_form():
        return redirect(url_for('login'))

    try:
        age = int(request.form.get('age', ''))
        height_cm = float(request.form.get('height_cm', ''))
        weight_kg = float(request.form.get('weight_kg', ''))
        manual_value = request.form.get('manual_calorie_target', '').strip()
        manual_target = int(manual_value) if manual_value else None
    except (TypeError, ValueError):
        session['dashboard_error'] = 'Enter valid numbers for your nutrition profile.'
        return redirect(url_for('home'))

    estimate_sex = request.form.get('estimate_sex', '').strip()
    activity_level = request.form.get('activity_level', '').strip()
    valid_profile = (
        18 <= age <= 100
        and 120 <= height_cm <= 230
        and 35 <= weight_kg <= 350
        and estimate_sex in {'male', 'female'}
        and activity_level in ACTIVITY_MULTIPLIERS
        and (manual_target is None or 1000 <= manual_target <= 6000)
    )
    if not valid_profile:
        session['dashboard_error'] = (
            'Check your profile values. Ruby currently supports adult estimates '
            'and calorie targets from 1,000 to 6,000.'
        )
        return redirect(url_for('home'))

    estimated_calories = estimate_maintenance_calories(
        age, estimate_sex, height_cm, weight_kg, activity_level
    )
    with get_database() as connection:
        connection.execute(
            '''
            INSERT INTO nutrition_profiles (
                user_id, age, estimate_sex, height_cm, weight_kg,
                activity_level, estimated_calories, manual_calorie_target
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                age = excluded.age,
                estimate_sex = excluded.estimate_sex,
                height_cm = excluded.height_cm,
                weight_kg = excluded.weight_kg,
                activity_level = excluded.activity_level,
                estimated_calories = excluded.estimated_calories,
                manual_calorie_target = excluded.manual_calorie_target,
                updated_at = CURRENT_TIMESTAMP
            ''',
            (
                get_user_id(), age, estimate_sex, height_cm, weight_kg,
                activity_level, estimated_calories, manual_target,
            ),
        )
    session['dashboard_notice'] = 'Your nutrition targets were updated.'
    return redirect(url_for('home'))

@app.post('/nutrition/recipes/<int:saved_recipe_id>')
def log_saved_recipe(saved_recipe_id):
    if not session.get('username') or not valid_auth_form():
        return redirect(url_for('login'))

    user_id = get_user_id()
    with get_database() as connection:
        saved_recipe = connection.execute(
            '''
            SELECT * FROM saved_recipes
            WHERE id = ? AND user_id = ?
            ''',
            (saved_recipe_id, user_id),
        ).fetchone()
    if not saved_recipe:
        session['dashboard_error'] = 'That saved recipe could not be found.'
        return redirect(url_for('home'))

    try:
        recipe = get_recipe_details(
            saved_recipe['spoonacular_id'],
            include_nutrition=True,
        )
        nutrients = recipe_nutrients(recipe)
    except Exception:
        app.logger.exception('Failed to load saved recipe nutrition')
        session['dashboard_error'] = (
            'Ruby could not load nutrition for that recipe right now.'
        )
        return redirect(url_for('home'))

    if nutrients['calories'] <= 0:
        session['dashboard_error'] = 'Nutrition data is unavailable for that recipe.'
        return redirect(url_for('home'))

    with get_database() as connection:
        connection.execute(
            '''
            INSERT INTO food_log (
                user_id, saved_recipe_id, title, calories, protein_g,
                carbs_g, fat_g, fiber_g, calcium_mg, iron_mg,
                potassium_mg, vitamin_c_mg, vitamin_d_mcg, log_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                user_id,
                saved_recipe_id,
                saved_recipe['title'],
                nutrients['calories'],
                nutrients['protein_g'],
                nutrients['carbs_g'],
                nutrients['fat_g'],
                nutrients['fiber_g'],
                nutrients['calcium_mg'],
                nutrients['iron_mg'],
                nutrients['potassium_mg'],
                nutrients['vitamin_c_mg'],
                nutrients['vitamin_d_mcg'],
                date.today().isoformat(),
            ),
        )
    session['dashboard_notice'] = f"Added {saved_recipe['title']} to today."
    return redirect(url_for('home'))

@app.get('/api/nutrition/foods')
def search_nutrition_foods():
    if not session.get('username'):
        return jsonify({'error': 'Authentication required'}), 401
    query = request.args.get('q', '').strip()[:80]
    if len(query) < 2:
        return jsonify({'results': []})
    if food_search_rate_limited():
        return jsonify({'error': 'Too many searches. Please wait a minute.'}), 429
    try:
        return jsonify({'results': search_usda_foods(query)})
    except requests.RequestException:
        app.logger.exception('USDA food search failed')
        return jsonify({'error': 'Food search is temporarily unavailable.'}), 502

@app.post('/nutrition/foods')
def log_usda_food():
    if not session.get('username') or not valid_auth_form():
        return redirect(url_for('login'))
    try:
        fdc_id = int(request.form.get('fdc_id', ''))
        amount = float(request.form.get('amount', ''))
        unit = request.form.get('unit', 'grams')
        if unit not in {'grams', 'serving'}:
            raise ValueError('Choose grams or servings.')
        food = get_usda_food(fdc_id)
        values = food_log_values(food, amount, unit)
    except (TypeError, ValueError):
        session['dashboard_error'] = 'Choose a food and enter a valid amount.'
        return redirect(url_for('home'))
    except requests.RequestException:
        app.logger.exception('USDA food details failed')
        session['dashboard_error'] = (
            'Ruby could not load that food’s nutrition right now.'
        )
        return redirect(url_for('home'))

    with get_database() as connection:
        connection.execute(
            '''
            INSERT INTO food_log (
                user_id, fdc_id, title, amount_grams, calories, protein_g,
                carbs_g, fat_g, fiber_g, calcium_mg, iron_mg,
                potassium_mg, vitamin_c_mg, vitamin_d_mcg, log_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                get_user_id(),
                fdc_id,
                values['title'],
                values['amount_grams'],
                values['calories'],
                values['protein_g'],
                values['carbs_g'],
                values['fat_g'],
                values['fiber_g'],
                values['calcium_mg'],
                values['iron_mg'],
                values['potassium_mg'],
                values['vitamin_c_mg'],
                values['vitamin_d_mcg'],
                date.today().isoformat(),
            ),
        )
    session['dashboard_notice'] = f"Added {values['title']} to today."
    return redirect(url_for('home'))

@app.post('/nutrition/log/<int:food_log_id>/delete')
def delete_food_log(food_log_id):
    if not session.get('username') or not valid_auth_form():
        return redirect(url_for('login'))
    with get_database() as connection:
        connection.execute(
            'DELETE FROM food_log WHERE id = ? AND user_id = ?',
            (food_log_id, get_user_id()),
        )
    return redirect(url_for('home'))

@app.route('/recipe', methods=['GET', 'POST'])
def recipe():
    recipes = []
    error = None
    searched_ingredients = ""

    if request.method == 'POST':
        searched_ingredients = request.form.get('ingredients', '').strip()

        if not searched_ingredients:
            error = "Please enter at least one ingredient."
        else:
            try:
                recipes = search_recipes(searched_ingredients)

                if not recipes:
                    error = "No recipes were found for those ingredients."

            except requests.exceptions.HTTPError as http_error:
                status_code = http_error.response.status_code if http_error.response is not None else "unknown"
                app.logger.error("Spoonacular returned HTTP status %s", status_code)
                if status_code == 401:
                    error = "The Spoonacular API key was rejected."
                else:
                    error = "The recipe api returned an error."

            except requests.exceptions.RequestException:
                app.logger.exception("Spoonacular connection error")
                error = "The recipe api could not be reached."

            except Exception:
                app.logger.exception("Recipe search failed")
                error = "Recipe could not be loaded right now."

    return render_template(
        'recipe.html',
        recipes=recipes,
        searched_ingredients=searched_ingredients,
        error=error
    )

@app.route('/recipe/<int:recipe_id>')
def recipe_details(recipe_id):
    try:
        recipe = get_recipe_details(recipe_id)
        return render_template('recipe_details.html', recipe=recipe, error=None)
    except requests.exceptions.HTTPError as http_error:
        status_code = http_error.response.status_code if http_error.response is not None else None
        app.logger.error('Spoonacular recipe details returned HTTP status %s', status_code)
        if status_code == 401:
            error = 'The Spoonacular API key was rejected.'
        elif status_code == 402:
            error = 'The daily Spoonacular API limit has been reached.'
        elif status_code == 404:
            error = 'That recipe could not be found.'
        else:
            error = 'The recipe API returned an error.'
        return render_template('recipe_details.html', recipe=None, error=error)
    except requests.exceptions.RequestException:
        app.logger.exception('Spoonacular recipe details connection error')
        return render_template('recipe_details.html', recipe=None, error='The recipe API could not be reached.')
    except Exception:
        app.logger.exception('Recipe details failed')
        return render_template('recipe_details.html', recipe=None, error='The full recipe could not be loaded right now.')

@app.route('/routine', methods= ["GET", "POST"])
def routine():
    if request.method == "POST":
        api_key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GENAI_KEY')
        client = genai.Client(api_key=api_key)
        model = os.environ.get('GEMINI_MODEL', 'gemini-3.1-flash-lite')
        product = request.form.get("product_name")
        guidance = generate_routine_answer(client, model, product)
        return render_template('routine.html', routine=guidance.model_dump(), product_title=product)
    else:
        return render_template('routine.html', routine=None)


@app.route('/myruby', methods=["GET", "POST"])
def my_ruby():
    if request.method == "POST":
        new_body = request.json
        user_id = get_user_id()
        with get_database() as connection:
            cursor = connection.execute(
                'INSERT INTO routines (user_id, introduction, product_type, frequency) VALUES (?, ?, ?, ?)',
                (user_id, new_body['introduction'], new_body['product_type'], new_body['frequency'])
            )
            routine_id = cursor.lastrowid

            for step in new_body['routine_steps']:
                connection.execute(
                    'INSERT INTO routine_steps (routine_id, product, reason, tip) VALUES (?, ?, ?, ?)',
                    (routine_id, step['product'], step['reason'], step['tip'])
                )

            for note in new_body['steps']:
                connection.execute(
                    'INSERT INTO routine_notes (routine_id, text, type) VALUES (?, ?, ?)',
                    (routine_id, note, 'step')
                )

            for warning in new_body['warnings']:
                connection.execute(
                    'INSERT INTO routine_notes (routine_id, text, type) VALUES (?, ?, ?)',
                    (routine_id, warning, 'warning')
                )
        return {'status': 'saved'}, 200

    user_id = get_user_id()
    with get_database() as connection:
        routines = connection.execute(
            'SELECT * FROM routines WHERE user_id = ? ORDER BY id DESC',
            (user_id,)
        ).fetchall()

        saved_recipes = connection.execute(
            '''
            SELECT *
            FROM saved_recipes
            WHERE user_id = ?
            ORDER BY id DESC
            ''',
            (user_id,)
        ).fetchall()

        routine_ids = [r['id'] for r in routines]
        steps_by_routine = {}
        notes_by_routine = {}

        if routine_ids:
            placeholders = ','.join('?' for _ in routine_ids)

            steps = connection.execute(
                f'SELECT * FROM routine_steps WHERE routine_id IN ({placeholders}) ORDER BY id',
                routine_ids
            ).fetchall()
            for step in steps:
                steps_by_routine.setdefault(step['routine_id'], []).append(step)

            notes = connection.execute(
                f'SELECT * FROM routine_notes WHERE routine_id IN ({placeholders}) ORDER BY id',
                routine_ids
            ).fetchall()
            for note in notes:
                notes_by_routine.setdefault(note['routine_id'], []).append(note)

    saved_routines = []
    for routine in routines:
        saved_routines.append({
            'id': routine['id'],
            'introduction': routine['introduction'],
            'product_type': routine['product_type'],
            'frequency': routine['frequency'],
            'routine_steps': steps_by_routine.get(routine['id'], []),
            'notes': notes_by_routine.get(routine['id'], [])
        })



    return render_template('myruby.html', saved_routines=saved_routines, saved_recipes=saved_recipes)

@app.post('/myruby/<int:routine_id>/delete')
def delete_routine(routine_id):
    user_id = get_user_id()
    with get_database() as connection:
        routine = connection.execute(
            'SELECT id FROM routines WHERE id = ? AND user_id = ?',
            (routine_id, user_id)
        ).fetchone()
        if routine:
            connection.execute('DELETE FROM routine_notes WHERE routine_id = ?', (routine_id,))
            connection.execute('DELETE FROM routine_steps WHERE routine_id = ?', (routine_id,))
            connection.execute('DELETE FROM routines WHERE id = ? AND user_id = ?', (routine_id, user_id))
            return {'status': 'deleted'}, 200
    return {'status': 'not found'}, 404
            


@app.route('/drug-comparison')
def drug_comparison():
    data = session.pop('drug_comparison', None)
    if data:
        return render_template('drug_comparison.html', **data)
    return render_template('drug_comparison.html')


@app.post('/compare-drugs')
def compare_drugs():
  drug1 = request.form.get('drug1', '').strip()
  drug2 = request.form.get('drug2', '').strip()

  if not drug1 or not drug2:
    return redirect(url_for('drug_comparison'))

  comparison = generate_drug_comparison(drug1, drug2)

  return render_template('drug_comparison.html',
                          comparison=comparison,
                          drug1=drug1,
                          drug2=drug2)

@app.post('/myruby/recipe/<int:recipe_id>/save')
def save_recipe(recipe_id):
    user_id = get_user_id()

    try:
        recipe = get_recipe_details(recipe_id)

        with get_database() as connection:
            connection.execute(
                '''
                INSERT OR IGNORE INTO saved_recipes
                (user_id, spoonacular_id, title, image)
                VALUES (?, ?, ?, ?)
                ''',
                (
                    user_id,
                    recipe_id,
                    recipe.get('title', 'Untitled Recipe'),
                    recipe.get('image', '')
                )
            )
        return {'status': 'saved'}, 200

    except Exception:
        app.logger.exception('Failed to save recipe')
        return {'status': 'error'}, 500

@app.post('/myruby/recipe/<int:saved_recipe_id>/delete')
def delete_saved_recipe(saved_recipe_id):
    user_id = get_user_id()

    with get_database() as connection:
        deleted = connection.execute(
            '''
            DELETE FROM saved_recipes
            WHERE id = ? AND user_id = ?  
            ''',
            (saved_recipe_id, user_id)
        )

    if deleted.rowcount:
        return {'status': 'deleted'}, 200

    return {'status': 'not found'}, 404
