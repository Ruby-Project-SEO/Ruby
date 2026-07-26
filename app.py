import os
import git
import secrets
import sqlite3
import requests
import time
import hashlib
from collections import defaultdict, deque
from datetime import timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from flask import Flask, render_template, url_for, redirect, request, session
from flask_behind_proxy import FlaskBehindProxy
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field
from ruby import search_recipes, get_recipe_details, generate_drug_comparison



from ruby import (generate_food_remedies,
                  generate_drug_remedies,
                  generate_cosmetic_remedies,
                  select_item,
                  show_db,
                  delete_saved,
                  get_link)

app = Flask(__name__)
proxied = FlaskBehindProxy(app)
DATABASE = Path(app.instance_path) / 'ruby.db'
SECRET_FILE = Path(app.instance_path) / 'secret_key'
SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_PUBLISHABLE_KEY = os.environ.get('SUPABASE_PUBLISHABLE_KEY', '')
GOOGLE_CLIENT_ID = os.environ.get(
    'GOOGLE_CLIENT_ID',
    '725724612296-hc0jlt97b7a1bs5svmobbdo7o6p101sv.apps.googleusercontent.com',
)
LOGIN_ATTEMPTS = defaultdict(deque)

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
    session.clear()
    session.permanent = True
    session['user_id'] = user.get('id')
    session['username'] = display_name
    session['user_email'] = user.get('email')

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

def get_user_id():
    if 'user_id' not in session:
        session['user_id'] = uuid4().hex
    return session['user_id']

def add_activity(connection, user_id, description):
    connection.execute(
        'INSERT INTO activities (user_id, description) VALUES (?, ?)',
        (user_id, description)
    )

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
    ruby_response = session.pop('ruby_response', None)
    completed_count = sum(task['completed'] for task in tasks)
    context = {
        'username': session.get('username'),
        'tasks': tasks,
        'completed_count': completed_count
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



@app.route("/update_server", methods=['POST'])
def webhook():
    if request.method == 'POST':
        repo = git.Repo('/home/wellness/Ruby')
        origin = repo.remotes.origin
        origin.pull()
        return 'Updated PythonAnywhere successfully', 200
    else:
        return 'Wrong event type', 400

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


if __name__ == '__main__':
      app.run(debug=True, host="0.0.0.0")
