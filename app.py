from flask import Flask, render_template, url_for, redirect, request, session
from flask_behind_proxy import FlaskBehindProxy

app = Flask(__name__)
proxied = FlaskBehindProxy(app)
app.config['SECRET_KEY'] = 'c29bcfa698752666def85f68880d22d8'

@app.route('/')
def home():
    return render_template('welcome.html', username=session.get('username'))

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/entry', methods= ["GET", "POST"])
def entry():
    if request.method == "POST":
        session['username'] = request.form["usr"]
        return redirect(url_for('home'))
    return redirect(url_for('login'))

@app.route('/recipe')
def recipe():
    return render_template('recipe.html')



if __name__ == '__main__':
      app.run(debug=True, host="0.0.0.0")
