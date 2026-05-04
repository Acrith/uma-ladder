# Repository Audit — Uma Ladder Rebuild

Date: 2026-05-04
Scope: Milestone 0 (audit + decision) per `PROJECT_INTENTIONS.md` §18.

## 1. Local working tree

The working directory at `/home/r_krawczak/workspace/uma-ladder/` is currently empty
except for `PROJECT_INTENTIONS.md`. There is no local git repo yet. Per the
project rules, branch creation and the initial commit are left to the human.

## 2. Upstream prototype (`github.com/Acrith/uma-ladder`)

Inspected via the GitHub API on 2026-05-04. Default branch `main`, last push
2025-07-16. Top level:

```
Dockerfile         Procfile        fly.toml
app.py             init_db.py      clear_results.py
requirements.txt   ladder.b64      ladder.db
static/uploads/
templates/         (auth/, base.html, schedule.html, results.html, ladder.html,
                    player_profile.html, edit_race.html, edit_result.html, …)
```

`requirements.txt` pins only: Flask, Flask-SQLAlchemy, Flask-Login, itsdangerous,
Werkzeug, gunicorn. Deployment is Fly.io via Docker; `Procfile` runs gunicorn
against `app:app`.

`app.py` (~26 KB, single file) contains everything:

- **Models**: `User` (with `role` ∈ user/editor/admin/superadmin), `Race`,
  `RaceSignup`, `Result`. `Result` has duplicated `user_id` column.
- **Auth**: Flask-Login sessions; `@role_required(*roles)` decorator;
  password-reset via `URLSafeTimedSerializer`.
- **Scoring**: tiered fixed table by participant count, with grade multiplier
  (G1 1.1, G2/G3 1.0, OP 0.9). `calculate_points()` and
  `recalculate_results_for_race()` mutate result rows in place.
- **Race flow**: add/edit/delete race (editor+), 3-signups-per-week limit,
  manual result entry with Uma stats (speed/stamina/power/guts/wisdom), claim
  unclaimed results, basic player profile aggregation.
- **Uploads**: `static/uploads/` with UUID naming; URLs stored on `Result`.
- **Not present**: Discord webhooks, OCR, Elo, draft PvP, seasons, race
  presets, character table.
- **Brittle**: hardcoded `SECRET_KEY = 'uma-ladder-config'`; hardcoded
  `/data/ladder.db` and `/data/uploads`; `datetime.utcnow()` (deprecated);
  missing imports filled at runtime; 30-iteration DB-wait loop on startup;
  templates and routes intermixed with business logic.
- **Checked-in artifacts that should not be reused**: `ladder.db`,
  `ladder.b64` (base64 of the DB), `__pycache__/`.

## 3. Reuse conceptually

These ideas survive the rewrite intact:

- Role hierarchy `user → editor → admin → superadmin`. The new app extends it
  with `organizer` between user and editor (per intentions §6).
- Per-result Uma-stat columns (speed/stamina/power/guts/wisdom) and strategy.
- Grade multiplier as an *option* on official scoring — start without it for
  MVP simplicity (intentions §9 recommends simple flat points first), but
  keep the scoring rule centralized so a multiplier can plug in.
- Image upload with UUID filenames, stored under `static/uploads/` (or
  `/data/uploads/` in prod).
- Result claiming for unlinked legacy entries — implement deliberately with
  audit trail (intentions §8).
- Password reset via `itsdangerous` timed tokens.

## 4. Discard

- **Single-file `app.py`** — replaced by app-factory + blueprints + service
  modules (intentions §16).
- **Hardcoded secrets and paths** — replaced by `config.py` reading env vars,
  with separate Dev/Test/Prod configs.
- **Tiered participant-count scoring** — replaced by the simple placement
  table in intentions §9 (1st=10, 2nd=8, …) until the human asks otherwise.
  This is open question §20.1 and the recommended default answer is "simple
  points." The old tiered logic and grade multipliers are deferred behind a
  service interface.
- **`ladder.db` / `ladder.b64` checked into the repo** — production data does
  not belong in the repo. Migrations + seed commands replace this.
- **`init_db.py` / `clear_results.py` ad-hoc scripts** — replaced by Flask
  CLI commands and Alembic migrations.
- **Inline `datetime.utcnow()`** — replaced by `datetime.now(timezone.utc)`.
- **Result column duplication, missing imports** — fixed by writing fresh.

## 5. Open data questions inherited

Old `Race` has fields the new design splits across `Season`, `RacePreset`, and
`OfficialRace`. There is no migration path planned (intentions §20.11 default:
do not migrate old data). This is a clean rebuild.

## 6. Audit conclusion

The prototype confirms that the domain (races, signups, results, scoring,
roles, uploads) is well-understood, but the structure is unsalvageable as a
foundation. Rebuild from scratch. Treat the old `app.py` as a reference for
behavior questions only — never as code to port.
