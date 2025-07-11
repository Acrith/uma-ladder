from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
import os

app = Flask(__name__)

# Setup database
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'ladder.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Define Race model
class Race(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    season = db.Column(db.String(50), nullable=False)
    week = db.Column(db.Integer, nullable=False)
    race_number = db.Column(db.Integer, nullable=False)
    race_type = db.Column(db.String(20), nullable=False)
    race_name = db.Column(db.String(100), nullable=False)
    grade = db.Column(db.String(10), nullable=False)
    event_type = db.Column(db.String(20), nullable=False)
    distance = db.Column(db.String(10), nullable=False)
    location = db.Column(db.String(50), nullable=False)
    surface = db.Column(db.String(20), nullable=False)
    direction = db.Column(db.String(10), nullable=False)
    mood = db.Column(db.String(20), nullable=False)
    weather = db.Column(db.String(20), nullable=False)

class Result(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    
    race_id = db.Column(db.Integer, db.ForeignKey('race.id'), nullable=False)
    player_name = db.Column(db.String(50), nullable=False)
    uma_name = db.Column(db.String(50), nullable=False)
    placement = db.Column(db.Integer, nullable=False)
    points = db.Column(db.Integer, nullable=False)

    # Optional: Relationship for convenience
    race = db.relationship('Race', backref='results')

def load_schedule():
    with open("data/schedule.json", "r", encoding="utf-8") as f:
        return json.load(f)

@app.route('/schedule')
def schedule():
    races = Race.query.order_by(Race.week, Race.id).all()
    return render_template("schedule.html", races=races)

@app.route('/ladder')
def ladder():
    leaderboard = (
        db.session.query(
            Result.player_name,
            func.sum(Result.points).label('total_points')
        )
        .group_by(Result.player_name)
        .order_by(func.sum(Result.points).desc())
        .all()
    )

    return render_template("ladder.html", leaderboard=leaderboard)

@app.route('/')
def home():
    return render_template("index.html")

def run():
    app.run(debug=True)

if __name__ == '__main__':
    run()