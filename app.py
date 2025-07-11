from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, desc
import os
import uuid

app = Flask(__name__)
app.config['SECRET_KEY'] = 'uma-ladder-config'

# Setup database
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'ladder.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

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
    participant_count = db.Column(db.Integer, nullable=True)

class Result(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    
    race_id = db.Column(db.Integer, db.ForeignKey('race.id'), nullable=False)
    player_name = db.Column(db.String(50), nullable=False)
    uma_name = db.Column(db.String(50), nullable=False)
    placement = db.Column(db.Integer, nullable=False)
    points = db.Column(db.Integer, nullable=False)
    
    # Basic Uma stats
    uma_strategy = db.Column(db.String(50))
    uma_speed = db.Column(db.Integer)
    uma_stamina = db.Column(db.Integer)
    uma_power = db.Column(db.Integer)
    uma_guts = db.Column(db.Integer)
    uma_wisdom = db.Column(db.Integer)

    # Optional screenshot (hosted image URL for now)
    uma_image_url = db.Column(db.String(500))

    # Optional: Relationship for convenience
    race = db.relationship('Race', backref='results')

def load_schedule():
    with open("data/schedule.json", "r", encoding="utf-8") as f:
        return json.load(f)

@app.route('/schedule')
def schedule():
    races = Race.query.order_by(Race.week, Race.id).all()
    return render_template("schedule.html", races=races)

@app.route("/ladder")
def ladder():
    # Aggregate total points per player
    leaderboard = (
        db.session.query(
            Result.player_name,
            func.sum(Result.points).label("total_points")
        )
        .group_by(Result.player_name)
        .order_by(desc("total_points"))
        .all()
    )

    return render_template("ladder.html", leaderboard=leaderboard)

def parse_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None

@app.route("/upload_image", methods=["POST"])
def upload_image():
    image = request.files.get("image")
    if not image:
        return {"error": "No image found"}, 400

    ext = image.filename.rsplit(".", 1)[-1].lower()
    if ext not in {"png", "jpg", "jpeg", "webp"}:
        return {"error": "Invalid file type"}, 400

    filename = f"{uuid.uuid4().hex}.{ext}"
    upload_path = os.path.join("static", "uploads", filename)
    os.makedirs("static/uploads", exist_ok=True)
    image.save(upload_path)

    return {"url": f"/static/uploads/{filename}"}

@app.route('/results', methods=['GET', 'POST'])
def results():
    races = Race.query.order_by(Race.week, Race.race_number).all()

    if request.method == 'POST':
        race_id = request.form.get('race_id')
        player_name = request.form.get('player_name')
        uma_name = request.form.get('uma_name')
        placement = request.form.get('placement')
        image_url = request.form.get("pasted_image_url") or None

        # Validate inputs (basic check)
        if not (race_id and player_name and uma_name and placement):
            flash("Please fill in all fields.", "danger")
            return redirect(url_for('results'))

        # Convert placement to int
        placement_int = int(placement)

        # Get the race
        race = Race.query.get(int(race_id))
        grade = race.grade.upper()

        # Use the value from Race, or fallback to 18 if not yet set
        participant_count = race.participant_count or 18

        # --- Define Scoring Rules ---
        SCORING_RULES = {
            (9, 11): [10, 8, 6, 5, 4],
            (12, 13): [10, 8, 6, 5, 4, 3],
            (14, 15): [10, 8, 6, 5, 4, 3, 2],
            (16, 18): [10, 8, 6, 5, 4, 3, 2, 1],
        }
        GRADE_MULTIPLIERS = {
            "G1": 1.1,
            "G2": 1.0,
            "G3": 1.0,
            "OP": 0.9,
            "PRE-OP": 0.9
        }

        # Get base point list for that participant range
        base_points = []
        for (low, high), points in SCORING_RULES.items():
            if low <= participant_count <= high:
                base_points = points
                break
        if not base_points:
            base_points = [10, 8, 6, 5, 4, 3, 2, 1]  # fallback

        # Determine points
        if placement_int > len(base_points):
            calculated_points = 0
        else:
            base = base_points[placement_int - 1]
            multiplier = GRADE_MULTIPLIERS.get(grade, 1.0)
            calculated_points = round(base * multiplier)

        # Save result
        result = Result(
            race_id=int(race_id),
            player_name=player_name.strip(),
            uma_name=uma_name.strip(),
            placement=placement_int,
            points=calculated_points,
            uma_strategy=request.form.get("uma_strategy"),
            uma_speed=parse_int(request.form.get("uma_speed")),
            uma_stamina=parse_int(request.form.get("uma_stamina")),
            uma_power=parse_int(request.form.get("uma_power")),
            uma_guts=parse_int(request.form.get("uma_guts")),
            uma_wisdom=parse_int(request.form.get("uma_wisdom")),
            uma_image_url=image_url
        )
        # Optional uma fields

        db.session.add(result)
        db.session.commit()
        flash(f"✅ Result added: {calculated_points} points", "success")
        return redirect(url_for('results'))

    return render_template("results.html", races=races)

@app.route("/results/<int:race_id>")
def race_results(race_id):
    race = Race.query.get_or_404(race_id)
    results = (
        Result.query
        .filter_by(race_id=race.id)
        .order_by(Result.placement.asc())
        .all()
    )
    return render_template("race_results.html", race=race, results=results)

def recalculate_results_for_race(race):
    SCORING_RULES = {
        (9, 11): [10, 8, 6, 5, 4],
        (12, 13): [10, 8, 6, 5, 4, 3],
        (14, 15): [10, 8, 6, 5, 4, 3, 2],
        (16, 18): [10, 8, 6, 5, 4, 3, 2, 1],
    }
    GRADE_MULTIPLIERS = {
        "G1": 1.1,
        "G2": 1.0,
        "G3": 1.0,
        "OP": 0.9,
        "PRE-OP": 0.9
    }

    participant_count = race.participant_count or 18
    grade = race.grade.upper()
    multiplier = GRADE_MULTIPLIERS.get(grade, 1.0)

    # Get correct scoring tier
    base_points = []
    for (low, high), points in SCORING_RULES.items():
        if low <= participant_count <= high:
            base_points = points
            break
    if not base_points:
        base_points = [10, 8, 6, 5, 4, 3, 2, 1]

    # Update all results
    for result in Result.query.filter_by(race_id=race.id).all():
        if result.placement > len(base_points):
            result.points = 0
        else:
            result.points = round(base_points[result.placement - 1] * multiplier)

    db.session.commit()

@app.route("/edit_race/<int:race_id>", methods=["GET", "POST"])
def edit_race(race_id):
    race = Race.query.get_or_404(race_id)

    if request.method == "POST":
        try:
            participant_count = int(request.form.get("participant_count"))
            race.participant_count = participant_count
            db.session.commit()
            recalculate_results_for_race(race)
            flash("✅ Race updated successfully!", "success")
            return redirect(url_for("schedule"))
        except ValueError:
            flash("⚠ Please enter a valid number.", "danger")

    return render_template("edit_race.html", race=race)


@app.route('/')
def home():
    return render_template("index.html")

def run():
    app.run(debug=True)

if __name__ == '__main__':
    run()