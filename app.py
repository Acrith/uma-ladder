from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, redirect, abort, current_app
from flask_login import UserMixin, LoginManager, login_user, login_required, logout_user, current_user
from itsdangerous import URLSafeTimedSerializer
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, desc
from functools import wraps
import os
import uuid

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////data/ladder.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'uma-ladder-config'
db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

def generate_reset_token(username, expires_sec=3600):
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    return s.dumps(username, salt='password-reset')

def verify_reset_token(token, max_age=3600):
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        username = s.loads(token, salt='password-reset', max_age=max_age)
    except Exception:
        return None
    return username

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)

    role = db.Column(db.String(20), default='user')  

    results = db.relationship("Result", backref="user", lazy=True)  # 🔗 linked

    def is_admin(self):
        return self.role in ['admin', 'superadmin']

    def is_superadmin(self):
        return self.role == 'superadmin'

    def is_editor(self):
        return self.role in ['editor', 'admin', 'superadmin']

    def __repr__(self):
        return f"<User {self.username}>"

def role_required(*roles):
    def wrapper(fn):
        @wraps(fn)
        def decorated_view(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return decorated_view
    return wrapper

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
    invite_code = db.Column(db.String(255))  # Increased size to allow multiple codes
    
    @property
    def invite_codes(self):
        return [code.strip() for code in self.invite_code.split(',')] if self.invite_code else []

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

def calculate_points(race: Race, placement: int) -> int:
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

    base_points = []
    for (low, high), points in SCORING_RULES.items():
        if low <= participant_count <= high:
            base_points = points
            break
    if not base_points:
        base_points = [10, 8, 6, 5, 4, 3, 2, 1]

    if placement > len(base_points):
        return 0
    return round(base_points[placement - 1] * multiplier)

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
        result.points = calculate_points(race, result.placement)

    db.session.commit()

@app.route("/admin/users")
@login_required
@role_required('admin', 'superadmin')
def admin_users():
    users = [u for u in User.query.all() if u is not None]
    return render_template("admin_users.html", users=users)

@app.route("/admin/users/set_role/<int:user_id>", methods=["POST"])
@login_required
@role_required('admin', 'superadmin')
def set_user_role(user_id):
    user = User.query.get_or_404(user_id)
    new_role = request.form.get("role")

    if new_role not in ['user', 'editor', 'admin', 'superadmin']:
        flash("Invalid role selected.", "danger")
        return redirect(url_for("admin_users"))

    # Prevent unauthorized superadmin assignment
    if new_role == 'superadmin' and not current_user.is_superadmin():
        flash("Only superadmins can assign the superadmin role.", "danger")
        return redirect(url_for("admin_users"))

    # Prevent demoting yourself from superadmin
    if user.id == current_user.id and current_user.role == 'superadmin' and new_role != 'superadmin':
        flash("You cannot demote yourself from superadmin.", "warning")
        return redirect(url_for("admin_users"))

    user.role = new_role
    db.session.commit()
    flash(f"{user.username}'s role updated to {new_role}.", "success")
    return redirect(url_for("admin_users"))

@app.route("/admin/users/delete/<int:user_id>")
@login_required
@role_required('admin', 'superadmin')
def delete_user(user_id):
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You cannot delete yourself.", "warning")
        return redirect(url_for("admin_users"))

    # Prevent deleting superadmins unless you're one
    if user.role == 'superadmin' and not current_user.is_superadmin():
        flash("Only superadmins can delete another superadmin.", "danger")
        return redirect(url_for("admin_users"))

    # Optional: prevent admins from deleting other admins/editors
    if current_user.role == 'admin' and user.role in ['admin', 'editor', 'superadmin']:
        flash("Admins cannot delete other admins, editors, or superadmins.", "danger")
        return redirect(url_for("admin_users"))

    db.session.delete(user)
    db.session.commit()
    flash(f"User '{user.username}' has been deleted.", "success")
    return redirect(url_for("admin_users"))

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    username = verify_reset_token(token)
    if not username:
        flash("Invalid or expired token.", "danger")
        return redirect(url_for('login'))

    user = User.query.filter_by(username=username).first_or_404()

    if request.method == 'POST':
        new_password = request.form['password']
        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        flash("✅ Password updated.", "success")
        return redirect(url_for('login'))

    return render_template('reset_password.html')

@app.route('/admin/reset_link/<int:user_id>')
@login_required
@role_required('admin', 'superadmin')
def admin_generate_reset_link(user_id):
    user = User.query.get_or_404(user_id)
    token = generate_reset_token(user.username)
    reset_url = url_for('reset_password', token=token, _external=True)

    return render_template('admin_show_reset_link.html', user=user, reset_url=reset_url)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        if User.query.filter_by(username=username).first():
            flash("Username already exists", "danger")
            return redirect("/register")

        hashed_pw = generate_password_hash(password)
        user = User(username=username, password_hash=hashed_pw, role="user")
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
@role_required('editor', 'admin', 'superadmin')
def mark_race_completed(race_id):
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
@login_required
@role_required('editor', 'admin', 'superadmin')
def results():
    races = Race.query.order_by(Race.week, Race.race_number).all()

    if request.method == 'POST':
        race_id = request.form.get('race_id')
        player_name = request.form.get('player_name')
        uma_name = request.form.get('uma_name')
        placement = request.form.get('placement')
        image_url = request.form.get("pasted_image_url") or None

        if not (race_id and player_name and uma_name and placement):
            flash("Please fill in all fields.", "danger")
            return redirect(url_for('results'))

        placement_int = int(placement)
        race = Race.query.get(int(race_id))
        participant_count = race.participant_count or 18
        calculated_points = calculate_points(race, placement_int)

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

@app.route("/edit_race/<int:race_id>", methods=["GET", "POST"])
@login_required
@role_required("editor", "admin", "superadmin")
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
        race.invite_code = request.form.get("invite_code")

        db.session.commit()
        flash("Race updated!", "success")
        return redirect(url_for("schedule"))

    return render_template("edit_race.html", race=race)

@app.route('/edit_result/<int:result_id>', methods=['GET', 'POST'])
@login_required
@role_required("editor", "admin", "superadmin")
def edit_result(result_id):
    result = Result.query.get_or_404(result_id)

    if request.method == 'POST':
        result.player_name = request.form.get('player_name')
        result.uma_name = request.form.get('uma_name')
        result.placement = int(request.form.get('placement'))
        race = Race.query.get(result.race_id)
        result.points = calculate_points(race, result.placement)
        result.uma_strategy = request.form.get("uma_strategy")
        result.uma_speed = parse_int(request.form.get("uma_speed"))
        result.uma_stamina = parse_int(request.form.get("uma_stamina"))
        result.uma_power = parse_int(request.form.get("uma_power"))
        result.uma_guts = parse_int(request.form.get("uma_guts"))
        result.uma_wisdom = parse_int(request.form.get("uma_wisdom"))
        result.uma_image_url = request.form.get("pasted_image_url") or result.uma_image_url

        db.session.commit()
        flash("✅ Result updated.", "success")
        return redirect(url_for('race_results', race_id=result.race_id))

    return render_template('edit_result.html', result=result)

@app.route("/delete_race/<int:race_id>", methods=["POST"])
@login_required
@role_required("admin", "superadmin")
def delete_race(race_id):
    race = Race.query.get_or_404(race_id)

    # Also delete associated results if needed
    Result.query.filter_by(race_id=race.id).delete()

    db.session.delete(race)
    db.session.commit()
    flash("Race deleted successfully.", "success")
    return redirect(url_for("schedule"))

@app.route("/add_race", methods=["GET", "POST"])
@login_required
@role_required("editor", "admin", "superadmin")
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
            participant_count=int(request.form.get("participant_count") or 0),
            invite_code=request.form.get("invite_code")
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
@role_required('editor', 'admin', 'superadmin')
def delete_result(result_id):
    result = Result.query.get_or_404(result_id)
    db.session.delete(result)
    db.session.commit()
    flash("Result deleted successfully.", "success")

    # Redirect back to race result page if race ID was passed in form
    redirect_race_id = request.form.get("redirect_to_race")
    if redirect_race_id:
        return redirect(url_for('race_results', race_id=redirect_race_id))

    return redirect(url_for('results'))  # fallback

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