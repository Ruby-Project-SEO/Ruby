from flask import Flask, render_template, url_for, flash, redirect
from flask_behind_proxy import FlaskBehindProxy

app = Flask(__name__)
proxied = FlaskBehindProxy(app)
app.config['SECRET_KEY'] = 'c29bcfa698752666def85f68880d22d8'

@app.route('/')
def login():
    return render_template('login.html')

@app.route('/recipe')
def recipe():
    return render_template('recipe.html')


if __name__ == '__main__':
      app.run(debug=True, host="0.0.0.0")