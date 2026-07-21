import os
import secrets
import sqlite3
import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

from flask import Flask, render_template, url_for, redirect, request, session
from flask_behind_proxy import FlaskBehindProxy
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

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

class RubyGuidance(BaseModel):
    answer: str = Field(description='Concise educational wellness guidance in plain text.')
    ranked_categories: list[Literal['food', 'drugs', 'cosmetics']] = Field(
        min_length=3,
        max_length=3,
        description='Food, drugs, and cosmetics ranked from most to least relevant, each exactly once.'
    )

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
        activities = connection.execute(
            '''
            SELECT description, strftime('%m/%d %H:%M', created_at) AS display_time
            FROM activities
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 4
            ''',
            (user_id,)
        ).fetchall()
        ruby_response = connection.execute(
            'SELECT question, answer, ranking FROM ruby_responses WHERE user_id = ?',
            (user_id,)
        ).fetchone()
    completed_count = sum(task['completed'] for task in tasks)
    context = {
        'username': session.get('username'),
        'tasks': tasks,
        'activities': activities,
        'completed_count': completed_count
    }
    if ruby_response:
        context['ruby_question'] = ruby_response['question']
        context['ruby_answer'] = ruby_response['answer']
        context['ruby_ranking'] = json.loads(ruby_response['ranking'])
        context['ruby_options'] = session.get('ruby_options', [])
        context['ruby_category'] = session.get('ruby_category')
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
        model=model,
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=(
                'You are Ruby, a concise wellness information assistant. '
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

@app.route('/')
def home():
    return render_dashboard()

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/entry', methods= ["GET", "POST"])
def entry():
    if request.method == "POST":
        session['username'] = request.form["usr"]
        return redirect(url_for('home'))
    return redirect(url_for('login'))

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
        
        session['ruby_options'] = options
        session['ruby_category'] = top_category


        user_id = get_user_id()
        with get_database() as connection:
            connection.execute(
                '''
                INSERT INTO ruby_responses (user_id, question, answer, ranking, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    question = excluded.question,
                    answer = excluded.answer,
                    ranking = excluded.ranking,
                    updated_at = CURRENT_TIMESTAMP
                ''',
                (user_id, question, answer, json.dumps(ranking))
            )
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

@app.route('/recipe')
def recipe():
    return render_template('recipe.html')



if __name__ == '__main__':
      app.run(debug=True, host="0.0.0.0")
