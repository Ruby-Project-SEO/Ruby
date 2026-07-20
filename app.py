import os
import secrets
import sqlite3
from pathlib import Path
from uuid import uuid4

from flask import Flask, render_template, url_for, redirect, request, session
from flask_behind_proxy import FlaskBehindProxy

app = Flask(__name__)
proxied = FlaskBehindProxy(app)
DATABASE = Path(app.instance_path) / 'ruby.db'
SECRET_FILE = Path(app.instance_path) / 'secret_key'

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
        ''')

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

@app.route('/')
def home():
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
    completed_count = sum(task['completed'] for task in tasks)
    return render_template(
        'welcome.html',
        username=session.get('username'),
        tasks=tasks,
        activities=activities,
        completed_count=completed_count
    )

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/entry', methods= ["GET", "POST"])
def entry():
    if request.method == "POST":
        session['username'] = request.form["usr"]
        return redirect(url_for('home'))
    return redirect(url_for('login'))

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
