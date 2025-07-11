from flask import Flask, render_template

app = Flask(__name__)

# Static schedule for now (hardcoded list of dicts)
race_schedule = [
    {"week": 1, "race": "Centaur Stakes", "grade": "G2", "distance": "1200m", "track": "Chukyo", "notes": "Sprint"},
    {"week": 1, "race": "Naruo Kinen", "grade": "G3", "distance": "2000m", "track": "Hanshin", "notes": "Medium"},
    {"week": 1, "race": "Stayers Stakes", "grade": "G2", "distance": "3600m", "track": "Nakayama", "notes": "Long"},
]

@app.route('/schedule')
def schedule():
    return render_template("schedule.html", races=race_schedule)

@app.route('/')
def home():
    return render_template("index.html")

if __name__ == '__main__':
    app.run(debug=True)