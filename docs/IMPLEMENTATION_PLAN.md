# Implementation Plan — Uma Ladder Rebuild

This plan turns `PROJECT_INTENTIONS.md` §18 milestones into PR-sized steps.
Each PR is meant to be small enough to review in one sitting (target:
≤ ~400 lines of diff excluding lockfiles, templates, and seed data).

Conventions for every PR below:

- Tests land in the **same** PR as the code they cover, ideally before the
  complex behavior (write the test, watch it fail, make it pass).
- No hardcoded secrets — config reads from env via `config.py`.
- No external HTTP inside request handlers — importers and webhook senders
  live in service modules and are invoked from CLI commands or queued work.
- Service modules own business logic; route handlers translate request →
  service call → response.
- The human creates the branch, stages, commits, and opens the PR. Claude
  proposes the diff only.

---

## PR 1 — Project foundation (Milestone 1, slice 1)

Branch suggestion: `rewrite/project-foundation`.

Scope:

- `pyproject.toml` with deps from ADR 0001 (Flask, SQLAlchemy 2.x,
  Flask-Migrate, Flask-Login, Flask-WTF, python-dotenv, Jinja2 transitively,
  pytest, ruff). Pin only major.minor where it matters.
- `uma_ladder/__init__.py` with `create_app(config_object)` factory.
- `uma_ladder/extensions.py` exposing `db`, `migrate`, `login_manager`,
  `csrf`.
- `uma_ladder/config.py` with `BaseConfig`, `DevConfig`, `TestConfig`,
  `ProdConfig` reading from env. Secret key is **required** in Prod, has a
  dev-only default in Dev, and is fixed for Test.
- Empty blueprint registrations for `dashboard`, `auth`, `profiles`,
  `official`, `draft`, `presets`, `notifications`, `ocr` — each a stub with
  a single route that returns `"<area> placeholder"` so URL wiring is
  testable.
- `uma_ladder/templates/base.html` with HTMX `<script>` tag, Tailwind
  `<link>`, and a `{% block content %}` shell. No real navigation yet.
- `uma_ladder/cli.py` with a `flask uma seed --help` placeholder (no real
  seed yet).
- `migrations/` initialized via `flask db init` (committed empty).
- `tests/conftest.py` with an `app` fixture using `TestConfig` and an
  in-memory SQLite DB.
- `tests/test_smoke.py` covering: app factory builds, every blueprint's
  placeholder route returns 200, `/healthz` returns 200.
- `.env.example` listing every env var the app reads, with comments.
- `ruff` configured in `pyproject.toml`. CI is **not** added in this PR —
  defer to PR 2 once there's something worth running it on.
- `Dockerfile` and `fly.toml` are **not** in this PR. Defer to PR 8.

Out of scope: any models, any auth flow, any styling beyond Tailwind base,
any GitHub Actions config.

Done when:

- `flask --app uma_ladder run` starts locally.
- `pytest -q` passes.
- `ruff check .` passes.

Risks: choosing Tailwind via CDN vs CLI. Recommendation: CDN tag in PR 1
to keep diff small; switch to Tailwind CLI build in PR 8 (polish) when the
production Dockerfile lands.

---

## PR 2 — Auth, users, roles (Milestone 1, slice 2)

Scope:

- `models/users.py` with `User` (id, username, password_hash, role,
  is_active, timestamps). Role is a `str` enum column with the six values
  from intentions §6.
- Password hashing via `werkzeug.security`.
- `auth/` blueprint: register, login, logout, password-reset request,
  password-reset confirm. Forms use Flask-WTF. CSRF on by default.
- `services/auth.py` with `register_user`, `authenticate`, `issue_reset_token`,
  `consume_reset_token` — pure functions, no Flask globals.
- `services/permissions.py` with `role_required(*roles)` decorator and a
  `has_role(user, *roles)` helper.
- Alembic migration: `users` table.
- Tests:
  - service: bcrypt round-trip, role hierarchy helper, token round-trip,
    expired token rejection.
  - route: register → login → logout happy path; wrong password rejected;
    `role_required` rejects anonymous and lower roles.

Done when both service and route tests pass and a fresh `flask db upgrade`
creates the `users` table.

---

## PR 3 — Profiles + UmaCharacter seed (Milestone 2)

Scope:

- `models/profiles.py` (`UserProfile`) and `models/uma_character.py`.
- `profiles/` blueprint: view own profile, edit own profile, view public
  profile by username.
- `services/profiles.py` for fetch/update logic.
- `presets/seed_uma_characters.py` (CLI subcommand
  `flask uma seed-characters`) reading from a checked-in JSON snapshot
  under `data/seeds/uma_characters.json`. Idempotent (upsert by `slug`).
  Importer that fetches GameTora is **deferred** — PR 3 ships a small
  hand-curated subset so profile Oshi selection works.
- Tests: profile edit auth check (only owner edits), seed idempotency,
  Oshi link integrity.

---

## PR 4 — Seasons + race presets + custom-race seed (Milestone 3)

Scope:

- `models/seasons.py`, `models/presets.py`.
- `presets/seed_custom_races.py` (CLI `flask uma seed-presets`) parsing the
  appendix in §22 of `PROJECT_INTENTIONS.md`. Normalizes `InnerMax` /
  `OuterMax` formatting bugs. Idempotent by `(venue, surface,
  distance_meters, direction, course_variant)` natural key.
- `services/randomizer.py` — `pick_preset(pool, bans, rng)` pure function.
  Returns either a preset id or a `RandomizerError` describing which bans
  emptied the pool.
- Admin route to list/disable presets (read + toggle only — no create UI
  yet).
- Tests **before** the randomizer logic: every ban category empties the
  pool predictably; preset-pool=g1+custom respects `enabled=False`; seed
  parser rejects malformed lines with line numbers.

---

## PR 5 — Official race MVP (Milestone 4)

Scope:

- `models/official_races.py` (`OfficialRace`, `OfficialRaceRegistration`,
  `OfficialRaceResult`).
- `services/official.py` and `services/scoring.py`. Scoring is the simple
  table from intentions §9; grade multiplier is a feature flag defaulted
  off.
- Routes: organizer race create, registration open/close, room-code
  paste with `expires_at = now + 24h`, manual result entry, ladder view.
- Top-5 official ladder block on dashboard.
- Tests: scoring rule for placements 1–10, registration cap, room-code
  expiry boundary, ladder ordering with ties, `role_required('organizer')`
  on the right routes.

Defer to later PRs: Discord webhook trigger, OCR.

---

## PR 6 — Draft PvP MVP (Milestone 5)

Scope is large; split into 6a, 6b, 6c at review time if the diff exceeds
~600 lines:

- 6a: match create/join, Uma submission, ready check.
- 6b: ban phase + randomization (re-uses PR 4 randomizer).
- 6c: room-code, manual result, Elo update + history rows.

`services/elo.py` exposes `apply_match(winner_id, loser_id, k=32)` and
returns the rating-change rows ready to insert. Pure function. Tests
before code. State-machine transitions live in `services/draft.py` and
are tested with a parameterized matrix.

---

## PR 7 — Discord webhooks (Milestone 6)

Scope:

- `notifications/discord.py` with `send_embed(target, payload)`. Reads
  webhook URL from env per target (`DISCORD_WEBHOOK_*_URL`). Records every
  attempt in `DiscordNotificationAttempt`.
- Service hooks: official race published, official room code,
  official results, draft room code, draft results.
- Admin view listing failed attempts with retry button.
- Tests use a `responses` library or a fake transport. No live network.

---

## PR 8 — OCR assist (Milestone 7)

Scope:

- `ocr/providers.py` with `OcrProvider` ABC + `MockOcrProvider` returning
  fixture data + `ManualOcrProvider` (no-op, used as default).
- Upload + parse-attempt persistence.
- Confirmation UI: parsed rows with confidence, edit fields, only confirmed
  rows write to `OfficialRaceResult` / `DraftRaceResult`.
- Tests guarantee: no result row is written without a `confirmed_by_user_id`
  set.

---

## PR 9 — Google Vision OCR provider

Scope:

- `GoogleVisionOcrProvider` class behind the existing `OcrProvider` ABC.
- Uses `DOCUMENT_TEXT_DETECTION` via the Vision REST API with API-key
  auth (no service-account JSON to mount in production).
- New env var `GOOGLE_VISION_API_KEY` in `.env.example` and `BaseConfig`
  (defaults to `None`; provider raises a clear error when selected
  without a key).
- Row clustering: parse the response into `{placement, uma_name,
  strategy?}` rows by sorting words by Y-coordinate, grouping into rows
  by line spacing, and assigning columns by X-coordinate.
- Injectable HTTP transport (mirrors the Discord pattern) so tests use
  a `FakeVisionTransport` and never hit the network.
- Tests: response parsing happy path with a fixture Vision JSON,
  empty response handling, transport failure → attempt status=failed,
  missing API key → clear error in `run_parse`.

Out of scope: changing `OCR_PROVIDER` default away from `manual` —
flip that in production by env var after you've validated accuracy on
real screenshots.

Done when: pytest green, ruff clean, `OCR_PROVIDER=google_vision`
+ `GOOGLE_VISION_API_KEY=…` in `.env` produces a parsed attempt for an
uploaded screenshot in dev.

---

## OCR refinements (deferred until after deploy + real usage)

Future PRs once the product itself is stable and real screenshots
reveal where the human bottleneck actually is:

- **Layout templates + cropping** — define screenshot-relative regions
  (placement / name / stats columns), crop before calling Vision, merge
  per-region results. Replaces the naive Y-cluster + X-sort heuristic.
- **Dictionary matching** — fuzzy-match OCR'd names against the seeded
  `UmaCharacter` table (`name_en` and `name_jp`) so transcription noise
  is corrected automatically.
- **Stats parsing** — Speed/Stamina/Power/Guts/Wit extraction from the
  stats grid.
- **Auto-fill button** — post a confirmed parse directly into the
  appropriate `submit_results` endpoint as a pre-filled form.

Intentionally postponed: keep PR 10 focused on shipping the product,
then iterate on OCR ergonomics from real-usage feedback.

---

## PR 10 — Polish, Dockerfile, Fly deploy, Tailwind build (Milestone 8)

Scope:

- Switch Tailwind from CDN to CLI build.
- Production `Dockerfile` and `fly.toml` (no secrets committed).
- `docs/DEPLOY.md` with the Fly bring-up checklist.
- GameTora G1 importer (`flask uma import-g1-races --source-file …`) —
  reads a checked-in JSON snapshot, never live HTTP. Importer that
  *fetches* GameTora is a separate flagged subcommand documented for
  manual one-off use.
- `make smoke` script: build, migrate, seed, hit dashboard, register a user,
  create a draft, complete it.
- GitHub Actions: ruff + pytest on PR.

---

## Execution notes

- Every PR includes a "How to test" block in its description with the exact
  shell commands a reviewer can run locally.
- Every PR ends with an explicit "Suggested next step" pointing to the next
  PR in this plan, so the human can pick up the thread.
- If review feedback diverges from this plan, update this file in the same
  PR rather than letting it drift.
