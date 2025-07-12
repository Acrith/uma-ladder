from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, redirect, abort
from flask_login import UserMixin, LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, desc
import os
import uuid

app = Flask(__name__)
app.config['SECRET_KEY'] = 'uma-ladder-config'

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# Setup database
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'ladder.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

    results = db.relationship("Result", backref="user", lazy=True)  # 🔗 linked

    def __repr__(self):
        return f"<User {self.username}>"

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
    completed = db.Column(db.Boolean, default=False)

class Result(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    race_id = db.Column(db.Integer, db.ForeignKey('race.id'), nullable=False)
    player_name = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    uma_name = db.Column(db.String(100), nullable=False)
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
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    # Optional: Relationship for convenience
    race = db.relationship('Race', backref='results')

@app.route("/admin/users")
@login_required
def admin_users():
    if not current_user.is_admin:
        flash("Access denied: Admins only.", "danger")
        return redirect(url_for("home"))

    users = User.query.all()
    return render_template("admin_users.html", users=users)

@app.route("/admin/users/promote/<int:user_id>")
@login_required
def promote_user(user_id):
    if not current_user.is_admin:
        flash("Access denied.", "danger")
        return redirect(url_for("home"))

    user = User.query.get_or_404(user_id)
    user.is_admin = True
    db.session.commit()
    flash(f"{user.username} promoted to admin.", "success")
    return redirect(url_for("admin_users"))

@app.route("/admin/users/demote/<int:user_id>")
@login_required
def demote_user(user_id):
    if not current_user.is_admin:
        flash("Access denied.", "danger")
        return redirect(url_for("home"))

    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You cannot demote yourself.", "warning")
        return redirect(url_for("admin_users"))

    user.is_admin = False
    db.session.commit()
    flash(f"{user.username} demoted.", "success")
    return redirect(url_for("admin_users"))

@app.route("/admin/users/delete/<int:user_id>")
@login_required
def delete_user(user_id):
    if not current_user.is_admin:
        flash("Access denied.", "danger")
        return redirect(url_for("home"))

    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You cannot delete yourself.", "warning")
        return redirect(url_for("admin_users"))

    # Optional: prevent deleting other admins
    if user.is_admin:
        flash("You cannot delete another admin.", "warning")
        return redirect(url_for("admin_users"))

    db.session.delete(user)
    db.session.commit()
    flash(f"User '{user.username}' has been deleted.", "success")
    return redirect(url_for("admin_users"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        if User.query.filter_by(username=username).first():
            flash("Username already exists", "danger")
            return redirect("/register")

        hashed_pw = generate_password_hash(password)
        user = User(username=username, password_hash=hashed_pw)
        db.session.add(user)
        db.session.commit()

        flash("Account created! Please log in.", "success")
        return redirect("/login")

    return render_template("auth/register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            flash(f"Welcome, {user.username}!", "success")
            return redirect("/")
        else:
            flash("Invalid credentials", "danger")
            return redirect("/login")

    return render_template("auth/login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You’ve been logged out.", "info")
    return redirect("/")

def load_schedule():
    with open("data/schedule.json", "r", encoding="utf-8") as f:
        return json.load(f)

@app.route("/schedule")
def schedule():
    upcoming_races = Race.query.filter_by(completed=False).order_by(Race.week, Race.race_number).all()
    completed_races = Race.query.filter_by(completed=True).order_by(Race.week.desc(), Race.race_number.desc()).all()
    return render_template("schedule.html", upcoming_races=upcoming_races, completed_races=completed_races)

@app.route("/race/<int:race_id>/complete", methods=["POST"])
@login_required
def mark_race_completed(race_id):
    if not current_user.is_admin:
        abort(403)
    race = Race.query.get_or_404(race_id)
    race.completed = True
    db.session.commit()
    flash(f"Marked race '{race.race_name}' as completed.", "success")
    return redirect(url_for("schedule"))

@app.route("/ladder")
def ladder():
    from sqlalchemy import func

    # Group results by player and aggregate stats
    leaderboard = (
        db.session.query(
            Result.player_name,
            func.count(Result.id).label("races"),
            func.sum(Result.points).label("total_points"),
            func.avg(Result.points).label("avg_points")
        )
        .group_by(Result.player_name)
        .order_by(func.sum(Result.points).desc())
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
        # Assign user if logged in
        if current_user.is_authenticated and player_name.strip().lower() == current_user.username.lower():
            result.user_id = current_user.id

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
        race.season = request.form.get("season")
        race.week = int(request.form.get("week"))
        race.race_number = int(request.form.get("race_number"))
        race.race_name = request.form.get("race_name")
        race.grade = request.form.get("grade")
        race.race_type = request.form.get("race_type")
        race.event_type = request.form.get("event_type")
        race.distance = request.form.get("distance")
        race.location = request.form.get("location")
        race.surface = request.form.get("surface")
        race.direction = request.form.get("direction")
        race.mood = request.form.get("mood")
        race.weather = request.form.get("weather")
        race.participant_count = int(request.form.get("participant_count") or 0)

        db.session.commit()
        flash("Race updated!", "success")
        return redirect(url_for("schedule"))

    return render_template("edit_race.html", race=race)

@app.route("/delete_race/<int:race_id>", methods=["POST"])
def delete_race(race_id):
    race = Race.query.get_or_404(race_id)

    # Also delete associated results if needed
    Result.query.filter_by(race_id=race.id).delete()

    db.session.delete(race)
    db.session.commit()
    flash("Race deleted successfully.", "success")
    return redirect(url_for("schedule"))


@app.route("/add_race", methods=["GET", "POST"])
def add_race():
    if request.method == "POST":
        new_race = Race(
            season=request.form.get("season"),
            week=int(request.form.get("week")),
            race_number=int(request.form.get("race_number")),
            race_type=request.form.get("race_type"),
            race_name=request.form.get("race_name"),
            grade=request.form.get("grade"),
            event_type=request.form.get("event_type"),
            distance=request.form.get("distance"),
            location=request.form.get("location"),
            surface=request.form.get("surface"),
            direction=request.form.get("direction"),
            mood=request.form.get("mood"),
            weather=request.form.get("weather"),
            participant_count=int(request.form.get("participant_count") or 0)
        )

        db.session.add(new_race)
        db.session.commit()
        flash("Race successfully added!", "success")
        return redirect(url_for("schedule"))

    return render_template("add_race.html")



@app.route("/profile")
@login_required
def profile_redirect():
    return redirect(url_for("player_profile", player_name=current_user.username))

@app.route("/profile/<player_name>")
def player_profile(player_name):
    is_owner = current_user.is_authenticated and current_user.username.lower() == player_name.lower()

    # Results belonging to this name
    claimed_results = (
        Result.query
        .filter(Result.player_name.ilike(player_name))
        .filter(Result.user_id == current_user.id if is_owner else True)
        .join(Race)
        .order_by(Race.week, Race.race_number)
        .all()
    )

    unclaimed_results = []
    if is_owner:
        unclaimed_results = (
            Result.query
            .filter(Result.player_name.ilike(player_name), Result.user_id.is_(None))
            .join(Race)
            .order_by(Race.week, Race.race_number)
            .all()
        )

    # Stats
    total_points = sum(r.points for r in claimed_results)
    race_count = len(claimed_results)
    avg_points = round(total_points / race_count, 2) if race_count else 0
    win_count = sum(1 for r in claimed_results if r.placement == 1)
    avg_placement = round(sum(r.placement for r in claimed_results) / race_count, 2) if race_count else 0

    return render_template(
        "player_profile.html",
        player_name=player_name,
        results=claimed_results,
        total_points=total_points,
        avg_points=avg_points,
        race_count=race_count,
        win_count=win_count,
        avg_placement=avg_placement,
        unclaimed_results=unclaimed_results,
        is_owner=is_owner,
        active_tab="profile"
    )

@app.route("/claim/<int:result_id>", methods=["POST"])
@login_required
def claim_result(result_id):
    result = Result.query.get_or_404(result_id)

    if result.user_id is None and result.player_name.lower() == current_user.username.lower():
        result.user_id = current_user.id
        db.session.commit()
        flash("Result successfully claimed!", "success")
    else:
        flash("You can't claim this result.", "danger")

    return redirect(url_for("player_profile", player_name=current_user.username))

@app.route("/delete_result/<int:result_id>", methods=["POST", "GET"])
@login_required
def delete_result(result_id):
    if not current_user.is_admin:
        abort(403)  # Forbidden

    result = Result.query.get_or_404(result_id)
    db.session.delete(result)
    db.session.commit()
    flash("Result deleted successfully.", "success")
    return redirect(url_for("results"))

@app.route('/')
def home():
    return render_template("index.html")

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def run():
    app.run(debug=True)

if __name__ == '__main__':
    run()