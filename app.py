from flask import render_template, Flask, request, url_for

app = Flask(__name__)

@app.route('/')
def login():
    return render_template('login.html')

@app.route('/entry', methods= ["GET", "POST"])
def entry():
    if request.method == "POST":
       user = request.form["usr"]
       return render_template('welcome.html', username= user )
    else:
        return render_template('login.html')

@app.route('/recipe')
def recipe():
    return render_template('recipe.html')



if __name__ == '__main__':
      app.run(debug=True, host="0.0.0.0")