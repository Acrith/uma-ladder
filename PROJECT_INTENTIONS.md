# Uma Ladder Rebuild — Project Intentions for Claude Code

Status: planning document for a greenfield rebuild  
Target repository: `https://github.com/Acrith/uma-ladder`  
Primary implementation style: rebuild from scratch, selectively reusing proven ideas from the old app only when they still fit the new architecture.

---

## 1. Purpose

Uma Ladder is a community web app for organizing Uma Musume races, tracking seasonal ladders, managing player profiles, and supporting both official organizer-run races and player-created draft PvP matches.

The old project should be treated as a prototype. The new version should be cleaner, testable, maintainable, and easier to extend into more race formats later.

The finished app should support:

- Public home/dashboard view.
- Login and registration.
- User profiles with Uma-themed identity fields.
- Seasonal official race ladder.
- Seasonal draft PvP ladder.
- Organizer race setup and result submission.
- Player-created 1v1 draft matches.
- Discord webhook notifications.
- Persistent database-backed state.
- OCR-assisted result parsing, with manual confirmation before saving.
- Clean internal structure allowing later expansion to 2v2 and 3v3.

---

## 2. Important Claude Code working rules

Claude Code should follow these repository workflow rules:

1. Do not create branches.
2. Do not run `git add`.
3. Do not run `git commit`.
4. Do not add `.claude` files or Claude-specific metadata files to the repository.
5. The human user creates branches, reviews diffs, stages files, commits, pushes, and opens PRs.
6. Prefer small, reviewable changes over huge generated rewrites.
7. After each implementation step, provide:
   - changed files,
   - what changed,
   - how to test,
   - known risks,
   - suggested next step.
8. Do not hardcode secrets, Discord webhook URLs, app secret keys, database credentials, or production hostnames.
9. If external data is imported from GameTora, implement it as a deliberate import/seed step and document attribution/source assumptions.
10. Do not scrape external sites on every user request. Cache or seed data locally.

Before implementation starts, the human should create a branch manually, for example:

```bash
git checkout -b rewrite/project-foundation
```

---

## 3. Current repo assumptions

The existing repo appears to be a compact Flask app with:

- Flask, Flask-SQLAlchemy, Flask-Login, Werkzeug, itsdangerous, gunicorn.
- A single large `app.py`.
- SQLite-style persistence under `/data/ladder.db`.
- Upload handling under `/data/uploads` or static uploads.
- Existing ideas around users, roles, races, race signups, results, profiles, and point calculation.

Treat these as hints, not as architecture to preserve. The rewrite should avoid a single-file monolith.

---

## 4. Recommended technology direction

Claude may propose alternatives, but the recommended path is:

### Preferred stack

- Backend: Flask rebuilt properly with app factory pattern.
- Database: PostgreSQL for production, SQLite allowed for local dev only.
- ORM: SQLAlchemy 2.x.
- Migrations: Alembic or Flask-Migrate.
- Auth/session: Flask-Login.
- Forms/security: Flask-WTF or equivalent CSRF protection.
- Frontend: server-rendered Jinja templates plus HTMX for interactive flows.
- CSS: Tailwind CSS, Bootstrap, or another simple consistent UI system. Keep it practical.
- Testing: pytest.
- Deployment: Fly.io-compatible, using environment variables and a production database.
- Background work: avoid early heavy infrastructure. Start with DB-expiry checks and synchronous webhook calls with retry logging. Add RQ/Celery only when needed.

### Why this stack

This project is app-like but not complex enough to require a heavy SPA at the beginning. Server-rendered Flask with HTMX is easier to reason about, easier for Claude to modify safely, and close enough to the old app that some concepts can be reused without inheriting the old structure.

### Acceptable alternative

If Claude strongly recommends Django after repo audit, it may propose Django as an alternative because it gives auth, admin, migrations, and ORM structure out of the box. However, do not switch stacks automatically. First write a short architecture decision record comparing Flask and Django for this exact app.

---

## 5. Product overview

Uma Ladder has two main competitive modes:

### Official races

Organizer-created races. These are seasonal leaderboard events.

Expected behavior:

- Organizers create races from G1 presets or manual/custom presets.
- Players register for upcoming races.
- Organizer adds room code.
- Room code expires after 24 hours.
- Discord notification is sent when code is available.
- Race results are submitted by organizer/editor.
- OCR may help parse screenshot data, but human confirmation is required.
- Official seasonal ladder updates from saved results.

### Draft PvP races

Player-created 1v1 matches using draft/ban flow.

Expected behavior:

- Player creates a 1v1 draft match.
- Another player joins by code/link.
- Both players submit 2 or 3 Uma entries each.
- Match config locks whether each player submits 2 or 3 Umas.
- Both players ready check.
- Each player bans:
  - one submitted Uma,
  - one track condition.
- System selects a random G1 or custom race preset that respects track bans.
- Host creates in-game room and pastes room code.
- Room code expires after 24 hours.
- Discord notification tells players to submit their Umas.
- Result screenshot is uploaded.
- OCR suggests placements/stats/skills.
- Human confirms/corrects result.
- Draft PvP ladder updates with Elo.

---

## 6. User roles and permissions

Suggested roles:

### anonymous

- View home/dashboard.
- View public ladders.
- View public race schedule.
- View public user profiles.

### user

- Edit own profile.
- Register for official races.
- Create draft 1v1 match.
- Join draft match.
- Submit Uma draft entries.
- Ready check.
- Submit/confirm own match state where allowed.

### organizer

- Create/edit official races.
- Set room code for official race.
- Submit official results.
- Trigger Discord announcements for official race events.
- View organizer dashboard.

### editor

- Everything organizer can do.
- Correct results.
- Manage race presets if allowed.

### admin

- Manage users.
- Manage roles below admin.
- Manage seasons.
- Manage official/draft ladders.
- Manage presets and imported data.

### superadmin

- Full control.
- Can assign admin/superadmin.
- Can perform destructive maintenance actions.

Permission checks must exist server-side. Do not rely on hidden buttons only.

---

## 7. Main pages and UX

### 7.1 Layout

The app should have a clean navigation layout:

- Top navigation or side panel.
- Home/Dashboard.
- Races.
- Draft PvP.
- Ladders.
- Profiles/User search.
- Login/Register when logged out.
- User menu/profile/logout when logged in.
- Admin/Organizer links only when authorized.

Mobile should be usable, but desktop may be prioritized first.

### 7.2 Home screen / dashboard

Logged out dashboard should show:

- Upcoming official races.
- Upcoming or active draft matches, public-safe only if appropriate.
- Current seasonal official ladder top 5.
- Current seasonal draft PvP ladder top 5.
- Call to action: login/register.

Logged in dashboard should show:

- Same public info.
- User's upcoming race registrations.
- User's active draft matches.
- User's recent results.
- Quick links: create draft match, edit profile, view ladder.

---

## 8. User profiles

Each user should have a public profile page.

### Profile fields

Required account fields:

- username
- password hash
- role
- created_at
- updated_at

Profile fields:

- display_name
- avatar_url or uploaded avatar
- description / bio
- friend_code
- oshi_character_id
- optional Discord handle
- preferred display settings later

### Oshi support

Oshi should be selected from a locally seeded Uma character table.

Source data can be derived from GameTora's Uma Musume character list, but must be imported/cached locally and not fetched on every request.

Profile side panel should display:

- avatar,
- display name,
- friend code,
- selected Oshi,
- Oshi icon/image if allowed and available,
- description.

### Profile statistics

Profile should show:

- Draft PvP Elo rating.
- Current official season points.
- Last 5 draft match results.
- Last 5 official race results.
- Total official top 1 finishes.
- Total official top 2 finishes.
- Total official top 3 finishes.
- Highest win-rate official conditions:
  - distance category: Sprint, Mile, Medium, Long,
  - surface: Turf, Dirt,
  - optionally venue/direction later.

Note: the user wrote "Spring" once in the distance category list. Use the Uma Musume canonical "Sprint" unless the human says otherwise.

### Result claiming

If old/manual result entry allows player-name-only results, the new app should avoid ambiguity by linking results to user IDs whenever possible.

If result claiming is needed, implement it deliberately:

- only allow claiming unlinked results matching username/display name rules,
- admin/editor can resolve disputes,
- audit trail recommended.

---

## 9. Seasons and ladders

### Season model

Ladders are seasonal.

Default season length:

- 3 months per season.

Season fields:

- name
- starts_at
- ends_at
- status: planned, active, completed, archived
- created_by_user_id
- timestamps

Only one active season per ladder type should exist unless future design explicitly supports overlapping seasons.

### Official ladder

Official ladder is points-based by season.

Official leaderboard columns:

- rank
- player
- total points
- races entered
- wins/top1
- top2
- top3
- average placement
- win rate
- recent form

Initial scoring can reuse or adapt the old prototype's placement points, but the scoring rules must be centralized and tested.

Recommended first official scoring rule:

- 1st: 10
- 2nd: 8
- 3rd: 6
- 4th: 5
- 5th: 4
- 6th: 3
- 7th: 2
- 8th: 1
- below 8th: 0

Optional grade multiplier can be added later, but MVP may keep official G1 scoring simple.

### Draft PvP ladder

Draft ladder is Elo-based.

Recommended initial Elo:

- starting rating: 1000
- K-factor: 32
- match outcome:
  - win = 1
  - loss = 0
  - draw = 0.5 only if explicitly supported later

For 1v1 draft races with multiple submitted Umas:

- match config selects `umas_per_player` as 2 or 3,
- both players submit the same amount,
- each player bans one opponent Uma,
- winner is the player whose best remaining Uma finishes highest in the final race,
- future formats may replace this with a team scoring resolver.

Elo updates should be stored as immutable rating change records:

- player_id
- match_id
- season_id
- rating_before
- rating_after
- delta
- opponent_id
- outcome
- timestamp

Do not recompute Elo casually from scratch unless implementing a specific rebuild command.

---

## 10. Race presets

The system needs two preset sources:

1. G1 race presets imported from GameTora race data.
2. Built-in custom presets from the `[CUSTOM_RACES]` appendix in this document.

### Preset fields

A race preset should include:

- id
- source: g1_import, custom_builtin, manual
- external_source_url
- source_version / imported_at
- name
- grade
- venue
- surface: Turf, Dirt
- distance_meters
- distance_category: Sprint, Mile, Medium, Long
- direction: Left, Right, Straight, Stretch
- course_variant: Inner, Outer, Outer→Inner, etc.
- max_runners
- season if known
- weather/time defaults if needed
- enabled
- timestamps

### G1 race presets

G1 data should be salvaged/imported from GameTora's Uma Musume race list by filtering grade G1.

Implementation guidance:

- Do not hardcode fragile scraper logic directly into routes.
- Create a `seed_race_presets` or `import_gametora_races` command.
- Store imported data in local DB or checked-in JSON if licensing/attribution is acceptable.
- Make the import idempotent.
- Log imported, updated, skipped counts.
- Manual review of imported presets is acceptable.

### Custom presets

Custom presets are built into this project and should be seeded from the appendix.

The seed command should normalize formatting mistakes such as missing spaces in `InnerMax` / `OuterMax`.

### Random selection

Organizers and draft matches need random race selection.

Random selection inputs:

- preset pool:
  - G1 only,
  - custom only,
  - G1 + custom.
- banned conditions:
  - venue bans,
  - direction bans,
  - distance category bans,
  - surface bans if later added.
- max runners constraints.
- enabled presets only.

Random selection requirements:

- Use server-side random selection.
- Store selected preset on the race/match so it does not change after presentation.
- Store banned conditions and random seed/debug info if useful for auditability.
- If no eligible preset exists, return a clear error explaining which bans made the pool empty.

---

## 11. Official race setup flow

Organizer flow:

1. Open organizer race dashboard.
2. Create official race.
3. Choose active season.
4. Choose preset mode:
   - select G1 preset,
   - random G1 preset,
   - select custom preset,
   - random custom preset,
   - manual setup from fixed allowed values.
5. Configure date/time and registration settings.
6. Publish registration.
7. Players register.
8. Organizer creates in-game Room Match.
9. Organizer pastes room code.
10. App stores room code with `expires_at = now + 24 hours`.
11. App sends Discord webhook announcement.
12. Race is run.
13. Organizer uploads screenshot/results.
14. OCR suggests placements/stats/skills.
15. Organizer confirms/corrects.
16. Results are saved.
17. Official ladder updates.
18. Discord result webhook is sent.

Official race statuses:

- draft
- registration_open
- registration_closed
- room_code_pending
- room_code_available
- room_code_expired
- results_pending
- results_submitted
- completed
- cancelled

---

## 12. Draft PvP setup flow

### 12.1 Match creation

Player creates a match:

- chooses season,
- chooses `umas_per_player`: 2 or 3,
- chooses preset pool:
  - G1,
  - custom,
  - G1 + custom,
- receives join code/link,
- match status becomes `waiting_for_opponent`.

### 12.2 Joining

Second player joins by code/link.

Rules:

- cannot join own match as opponent,
- cannot join full match,
- cannot join expired/cancelled match,
- match status becomes `submitting_umas`.

### 12.3 Uma submission

Each player submits 2 or 3 Uma entries, based on match config.

Each submitted Uma entry should store:

- player_id
- uma_character_id if selected from known character list
- custom_uma_name fallback if needed
- optional build nickname
- optional screenshot
- notes
- locked_at when ready

When the player selected an Oshi in profile, that Oshi should be highlighted in the submission and opponent view. Use a neon/flashy border style, but keep it accessible and not too visually noisy.

### 12.4 Ready check

Each player confirms ready.

Once both ready:

- submissions are locked,
- match status becomes `ban_phase`.

### 12.5 Ban phase

Each player bans:

- one opponent Uma entry,
- one track condition.

Track condition ban types:

- direction: Left, Right, Straight/Stretch
- distance category: Sprint, Mile, Medium, Long
- venue/city: Sapporo, Hakodate, Fukushima, Niigata, Tokyo, Nakayama, Chukyo, Kyoto, Hanshin, Kokura, Oi/Ooi, etc.
- optionally surface later: Turf, Dirt

Ban requirements:

- ban choices are stored.
- ban choices are visible after both players lock bans.
- app must validate that each player submits exactly one Uma ban and one track condition ban.
- app must prevent duplicate/invalid conditions where appropriate.

### 12.6 Race randomization

After both bans are locked:

- app filters eligible presets by preset pool and bans,
- app randomly selects one preset,
- selected preset is stored,
- match status becomes `room_code_pending`.

If no preset remains, status becomes `randomization_failed` and the UI should show the reason. Admin/organizer may reset bans or allow reroll with adjusted rules.

### 12.7 Room code

Host creates in-game room and pastes code.

Rules:

- code expires after 24 hours,
- host can update code before results are submitted,
- both players are notified through Discord webhook if configured,
- match status becomes `room_code_available`.

### 12.8 Results

After race:

- result screenshot uploaded,
- OCR suggests placements/stats/skills,
- user/organizer confirms,
- match result is saved,
- draft Elo updates,
- Discord result webhook sent,
- status becomes `completed`.

Draft match statuses:

- waiting_for_opponent
- submitting_umas
- ready_check
- ban_phase
- randomizing_race
- randomization_failed
- room_code_pending
- room_code_available
- room_code_expired
- results_pending
- results_submitted
- completed
- cancelled

---

## 13. OCR-assisted results

OCR is important, but it should not be a blocker for the initial MVP.

### Principle

OCR should assist humans, not silently decide final results.

### OCR flow

1. User uploads screenshot.
2. App stores image.
3. OCR pipeline runs.
4. OCR creates a draft parse:
   - placements,
   - Uma names,
   - player names if visible,
   - stats: speed, stamina, power, guts, wit/wisdom,
   - strategy,
   - skills if available.
5. UI presents parsed rows with confidence.
6. Human edits/accepts rows.
7. Only confirmed rows become official saved results.

### OCR storage

Store:

- uploaded image record,
- OCR parse attempt,
- raw OCR text,
- structured extracted result,
- confidence if available,
- final confirmed result linkage.

### OCR implementation advice

Start with interfaces and manual fallback:

- `OcrProvider` interface.
- `ManualOcrProvider` / mock provider for tests.
- Optional future providers:
  - Tesseract,
  - EasyOCR,
  - cloud vision API,
  - LLM vision parser if acceptable.

Do not couple OCR provider to web route code.

---

## 14. Discord webhook notifications

Discord webhook integration should be environment-configured.

Environment variables:

- `DISCORD_WEBHOOK_RACE_REGISTRATION_URL`
- `DISCORD_WEBHOOK_OFFICIAL_RESULTS_URL`
- `DISCORD_WEBHOOK_DRAFT_RESULTS_URL`
- optional general fallback webhook

Never commit real webhook URLs.

### Notification events

#### Race registration

Trigger when:

- official race is published,
- player registration opens,
- maybe when player signs up if organizers want that signal.

Message should include:

- race name,
- season,
- date/time,
- preset details,
- max runners,
- registration link.

#### Official race room code

Trigger when organizer adds room code.

Message should include:

- race name,
- room code,
- expiry time,
- registered players,
- link to race page.

#### Official results

Trigger after confirmed official result submission.

Message should include:

- race name,
- top placements,
- points awarded,
- season ladder link.

#### Draft room code

Trigger when draft host adds room code.

Message should include:

- host,
- opponent,
- selected preset,
- room code,
- expiry time,
- submit Uma reminder.

#### Draft results

Trigger after confirmed draft result.

Message should include:

- winner,
- loser,
- selected preset,
- Elo changes,
- top placements,
- draft ladder link.

### Notification reliability

Store notification attempts in DB:

- event_type
- payload summary
- webhook target name
- status: pending, sent, failed
- response code/error
- timestamps

MVP may send synchronously, but failed sends should be visible to admins.

---

## 15. Suggested database model

Names are suggestions. Claude may refine after choosing framework.

### Core

#### User

- id
- username
- email optional
- password_hash
- role
- is_active
- created_at
- updated_at

#### UserProfile

- id
- user_id
- display_name
- avatar_url
- description
- friend_code
- discord_handle
- oshi_character_id
- created_at
- updated_at

#### UmaCharacter

- id
- source
- external_id
- slug
- name_en
- name_jp optional
- image_url optional
- profile_url optional
- enabled
- imported_at
- created_at
- updated_at

### Seasons and presets

#### Season

- id
- name
- starts_at
- ends_at
- official_enabled
- draft_enabled
- status
- created_by_user_id
- timestamps

#### RacePreset

- id
- source
- external_source_url
- name
- grade
- venue
- surface
- distance_meters
- distance_category
- direction
- course_variant
- max_runners
- enabled
- imported_at
- timestamps

### Official races

#### OfficialRace

- id
- season_id
- preset_id nullable for manual
- name
- status
- organizer_user_id
- scheduled_at
- registration_opens_at
- registration_closes_at
- room_code
- room_code_expires_at
- max_players
- notes
- timestamps

#### OfficialRaceRegistration

- id
- official_race_id
- user_id
- status: registered, cancelled, waitlisted
- created_at
- updated_at

#### OfficialRaceResult

- id
- official_race_id
- user_id
- uma_character_id nullable
- uma_name
- placement
- points
- strategy
- speed
- stamina
- power
- guts
- wisdom
- source_image_id nullable
- confirmed_by_user_id
- created_at
- updated_at

### Draft PvP

#### DraftMatch

- id
- season_id
- status
- host_user_id
- opponent_user_id
- join_code
- umas_per_player
- preset_pool
- selected_preset_id
- room_code
- room_code_expires_at
- winner_user_id
- loser_user_id
- completed_at
- timestamps

#### DraftMatchUmaEntry

- id
- draft_match_id
- user_id
- uma_character_id nullable
- custom_uma_name nullable
- build_nickname nullable
- screenshot_url nullable
- notes
- is_banned
- locked_at
- created_at
- updated_at

#### DraftMatchBan

- id
- draft_match_id
- user_id
- ban_type: uma, direction, distance_category, venue, surface
- banned_uma_entry_id nullable
- condition_key nullable
- locked_at
- created_at

#### DraftRaceResult

- id
- draft_match_id
- user_id
- uma_entry_id nullable
- placement
- strategy
- speed
- stamina
- power
- guts
- wisdom
- skills_json nullable
- source_image_id nullable
- confirmed_by_user_id
- created_at
- updated_at

#### DraftEloChange

- id
- draft_match_id
- season_id
- user_id
- opponent_user_id
- rating_before
- rating_after
- delta
- outcome
- created_at

### Shared

#### UploadedImage

- id
- uploader_user_id
- file_path or storage_key
- original_filename
- mime_type
- size_bytes
- purpose
- created_at

#### OcrParseAttempt

- id
- uploaded_image_id
- provider
- raw_text
- parsed_json
- confidence_json
- status
- error_message
- created_at

#### DiscordNotificationAttempt

- id
- event_type
- target_name
- payload_json
- status
- response_code
- error_message
- created_at
- sent_at

---

## 16. Suggested application structure

Preferred Flask structure:

```text
uma_ladder/
  __init__.py
  config.py
  extensions.py
  models/
    __init__.py
    users.py
    profiles.py
    seasons.py
    presets.py
    official_races.py
    draft_matches.py
    results.py
    notifications.py
    uploads.py
  auth/
    routes.py
    forms.py
  profiles/
    routes.py
    forms.py
    services.py
  dashboard/
    routes.py
  official/
    routes.py
    forms.py
    services.py
    scoring.py
  draft/
    routes.py
    forms.py
    services.py
    elo.py
    randomizer.py
  presets/
    routes.py
    importers.py
    seed_custom_races.py
  notifications/
    discord.py
    services.py
  ocr/
    providers.py
    services.py
    routes.py
  templates/
  static/
  cli.py
tests/
migrations/
```

Keep business logic in services, not directly inside route functions.

---

## 17. Testing requirements

Minimum tests:

### Unit tests

- official scoring rules,
- draft Elo calculations,
- draft match state transitions,
- ban validation,
- preset filtering/randomization,
- room code expiry rules,
- role permission helpers,
- seed/import normalization for custom races.

### Route tests

- anonymous dashboard works,
- register/login/logout works,
- profile edit permissions,
- official race creation requires organizer/editor/admin,
- regular user cannot create official race,
- player can create draft match,
- opponent can join draft match,
- user cannot join own draft match,
- room code cannot be submitted by unauthorized user,
- confirmed result updates ladder.

### Data tests

- seed custom races idempotently,
- GameTora import command can be mocked and does not require live network in tests,
- migrations create expected tables.

### Smoke tests

- app starts locally,
- database migration runs,
- seed command runs,
- dashboard loads,
- register user,
- create profile,
- create draft match,
- join draft match,
- complete minimal result flow.

---

## 18. Implementation milestones

### Milestone 0 — Repo audit and decision

Goal:

- inspect current repo,
- decide final stack,
- write `PROJECT_INTENTIONS.md` into repo,
- write architecture note.

Output:

- short audit document,
- proposed stack,
- first implementation branch plan.

Do not rewrite app yet.

### Milestone 1 — Clean app foundation

Goal:

- app factory,
- config via env,
- database setup,
- migrations,
- base layout,
- auth,
- roles,
- tests.

Done when:

- local app starts,
- tests pass,
- user can register/login/logout,
- role checks are covered by tests.

### Milestone 2 — Profiles and character seed

Goal:

- profile pages,
- avatar/description/friend code,
- Oshi selection,
- seeded UmaCharacter table,
- public profile stats shell.

Done when:

- user can edit own profile,
- profile displays selected Oshi,
- another user can view public profile.

### Milestone 3 — Seasons and race presets

Goal:

- Season model,
- RacePreset model,
- custom race seed command,
- optional GameTora G1 import command,
- preset management/admin page.

Done when:

- custom races seed idempotently,
- race preset randomizer is tested,
- banned condition filtering is tested.

### Milestone 4 — Official races MVP

Goal:

- organizer creates race,
- users register,
- organizer adds room code,
- room code expires,
- manual result entry,
- official ladder.

Done when:

- official race can be run end-to-end manually,
- official ladder top 5 appears on dashboard.

### Milestone 5 — Draft PvP 1v1 MVP

Goal:

- create match,
- join by code,
- submit 2 or 3 Umas,
- ready check,
- ban phase,
- random preset selection,
- room code,
- manual result confirmation,
- Elo update.

Done when:

- two users can complete a draft match end-to-end,
- draft ladder top 5 appears on dashboard,
- Elo changes are stored and visible.

### Milestone 6 — Discord notifications

Goal:

- webhook config,
- notification service,
- official race notifications,
- draft match notifications,
- result notifications,
- notification attempt logging.

Done when:

- webhook sends can be tested with fake/mocked endpoint,
- failed sends are visible in admin/log.

### Milestone 7 — OCR assist

Goal:

- upload screenshot,
- OCR provider interface,
- OCR parse attempt storage,
- confirmation UI,
- manual correction flow.

Done when:

- OCR result never writes final results without confirmation,
- manual fallback remains usable.

### Milestone 8 — Polish, deployment, admin quality

Goal:

- deployment docs,
- production env checklist,
- styling pass,
- admin pages,
- seed scripts,
- backup/export guidance.

Done when:

- app deploys cleanly,
- migrations and seeds work in production-like setup,
- critical paths have smoke tests.

---

## 19. Non-goals for MVP

Avoid these in the first implementation pass:

- full React SPA,
- real-time websockets for everything,
- automatic Discord bot commands,
- automatic OCR-only result saving,
- payment/subscription features,
- complicated tournament brackets,
- 2v2/3v3 implementation before 1v1 is stable,
- live scraping external sites on normal page loads,
- advanced anti-cheat systems.

---

## 20. Important open questions

Resolve these before or during early milestones:

1. Should official ladder use simple points, old app scoring, or another format?
2. Should official races also have Elo, or only draft PvP?
3. Should the app require email addresses or username/password only?
4. Who is allowed to submit official results?
5. Who is allowed to confirm draft results?
6. Should draft match result confirmation require both players or only host/admin?
7. Should room codes be public to all registered participants or only logged-in participants?
8. Should Discord notifications mention everyone/every role, or stay plain embeds?
9. Can GameTora images be displayed by hotlink, cached, or should only names/links be used?
10. What OCR provider is acceptable for deployment cost/privacy?
11. Should old data be migrated or discarded?
12. What is the exact season start date convention?
13. Should Oshi highlight be visible only during draft match entry, or everywhere profile Umas appear?

Recommended default answers for MVP:

- Official ladder uses simple points.
- Draft PvP uses Elo.
- Username/password only, email optional later.
- Organizer/editor/admin submits official results.
- Draft host submits results, opponent can dispute later; admin can correct.
- Room codes visible only to logged-in participants/organizers.
- Discord embeds should avoid mass pings until explicitly configured.
- Use names/links first for GameTora character data; handle images carefully.
- No old data migration unless human explicitly asks.

---

## 21. External data notes

Potential external sources:

- GameTora Uma Musume character list: `https://gametora.com/umamusume/characters`
- GameTora Uma Musume race list: `https://gametora.com/umamusume/races`
- GameTora racetrack list: `https://gametora.com/umamusume/racetracks`
- Existing repo: `https://github.com/Acrith/uma-ladder`

Important:

- GameTora says it is not affiliated with Uma Musume developers and that game materials are copyright Cygames.
- Treat external data carefully.
- Prefer storing source URLs and attribution.
- Avoid pretending GameTora data is official.
- Build importers in a way that can be re-run or corrected.

---

## 22. Custom race presets appendix

Seed these as `source = custom_builtin`.

Claude should parse and normalize them into structured rows with:

- venue
- surface
- distance_meters
- distance_category
- direction
- course_variant
- max_runners

Original project-provided list:

```text
Turf Races
Sapporo Turf 2600m (Long) Right Max Runners: 14
Sapporo Turf 2000m (Medium) Right Max Runners: 16
Sapporo Turf 1800m (Mile) Right Max Runners: 14
Sapporo Turf 1500m (Sprint) Right Max Runners: 14
Sapporo Turf 1200m (Sprint) Right Max Runners: 16
Hakodate Turf 2600m (Long) Right Max Runners: 16
Hakodate Turf 2000m (Medium) Right Max Runners: 16
Hakodate Turf 1800m (Mile) Right Max Runners: 16
Hakodate Turf 1200m (Sprint) Right Max Runners: 16
Hakodate Turf 1000m (Sprint) Right Max Runners: 16
Fukushima Turf 2600m (Long) Right Max Runners: 16
Fukushima Turf 2000m (Medium) Right Max Runners: 16
Fukushima Turf 1800m (Mile) Right Max Runners: 16
Fukushima Turf 1200m (Sprint) Right Max Runners: 14
Niigata Turf 2400m (Medium) Left / Inner Max Runners: 18
Niigata Turf 2200m (Medium) Left / Inner Max Runners: 18
Niigata Turf 2000m (Medium) Left / Outer Max Runners: 18
Niigata Turf 2000m (Medium) Left / Inner Max Runners: 18
Niigata Turf 1800m (Mile) Left / Outer Max Runners: 18
Niigata Turf 1600m (Mile) Left / Outer Max Runners: 18
Niigata Turf 1400m (Sprint) Left / Inner Max Runners: 18
Niigata Turf 1200m (Sprint) Left / Inner Max Runners: 18
Niigata Turf 1000m (Sprint) Stretch Max Runners: 18
Tokyo Turf 3400m (Long) Left Max Runners: 18
Tokyo Turf 2500m (Long) Left Max Runners: 18
Tokyo Turf 2400m (Medium) Left Max Runners: 18
Tokyo Turf 2300m (Medium) Left Max Runners: 18
Tokyo Turf 2000m (Medium) Left Max Runners: 18
Tokyo Turf 1800m (Mile) Left Max Runners: 18
Tokyo Turf 1600m (Mile) Left Max Runners: 18
Tokyo Turf 1400m (Sprint) Left Max Runners: 18
Nakayama Turf 3600m (Long) Right / Inner Max Runners: 16
Nakayama Turf 2500m (Long) Right / Inner Max Runners: 16
Nakayama Turf 2200m (Medium) Right / Outer Max Runners: 18
Nakayama Turf 2000m (Medium) Right / Inner Max Runners: 18
Nakayama Turf 1800m (Mile) Right / Inner Max Runners: 16
Nakayama Turf 1600m (Mile) Right / Outer Max Runners: 16
Nakayama Turf 1200m (Sprint) Right / Outer Max Runners: 16
Kyoto Turf 2200m (Medium) Right / Outer Max Runners: 18
Kyoto Turf 2000m (Medium) Right / Inner Max Runners: 18
Kyoto Turf 1800m (Mile) Right / Outer Max Runners: 18
Kyoto Turf 1600m (Mile) Right / Outer Max Runners: 18
Kyoto Turf 1600m (Mile) Right / Inner Max Runners: 18
Kyoto Turf 1400m (Sprint) Right / Outer Max Runners: 18
Kyoto Turf 1400m (Sprint) Right / Inner Max Runners: 18
Kyoto Turf 1200m (Sprint) Right / Inner Max Runners: 18
Hanshin Turf 3200m (Long) Right / Outer→Inner Max Runners: 18
Hanshin Turf 3000m (Long) Right / Inner Max Runners: 16
Hanshin Turf 2600m (Long) Right / Outer Max Runners: 18
Hanshin Turf 2400m (Medium) Right / Outer Max Runners: 18
Hanshin Turf 2200m (Medium) Right / Inner Max Runners: 18
Hanshin Turf 2000m (Medium) Right / Inner Max Runners: 16
Hanshin Turf 1800m (Mile) Right / Outer Max Runners: 18
Hanshin Turf 1600m (Mile) Right / Outer Max Runners: 18
Hanshin Turf 1400m (Sprint) Right / Inner Max Runners: 18
Hanshin Turf 1200m (Sprint) Right / Inner Max Runners: 16
Kokura Turf 2600m (Long) Right Max Runners: 16
Kokura Turf 2000m (Medium) Right Max Runners: 18
Kokura Turf 1800m (Mile) Right Max Runners: 16
Kokura Turf 1200m (Sprint) Right Max Runners: 18

Dirt Races
Sapporo Dirt 1700m (Mile) Right Max Runners: 14
Hakodate Dirt 1700m (Mile) Right Max Runners: 14
Fukushima Dirt 1700m (Mile) Right Max Runners: 15
Fukushima Dirt 1150m (Sprint) Right Max Runners: 16
Niigata Dirt 1800m (Mile) Left Max Runners: 15
Niigata Dirt 1200m (Sprint) Left Max Runners: 15
Tokyo Dirt 2100m (Medium) Left Max Runners: 16
Tokyo Dirt 1600m (Mile) Left Max Runners: 16
Tokyo Dirt 1400m (Sprint) Left Max Runners: 16
Tokyo Dirt 1300m (Sprint) Left Max Runners: 16
Nakayama Dirt 1800m (Mile) Right Max Runners: 16
Nakayama Dirt 1200m (Sprint) Right Max Runners: 16
Chukyo Dirt 1800m (Mile) Left Max Runners: 16
Chukyo Dirt 1400m (Sprint) Left Max Runners: 16
Kyoto Dirt 1900m (Medium) Right Max Runners: 16
Kyoto Dirt 1800m (Mile) Right Max Runners: 16
Kyoto Dirt 1400m (Sprint) Right Max Runners: 16
Kyoto Dirt 1200m (Sprint) Right Max Runners: 16
Hanshin Dirt 2000m (Medium) Right Max Runners: 16
Hanshin Dirt 1800m (Mile) Right Max Runners: 16
Hanshin Dirt 1400m (Sprint) Right Max Runners: 16
Kokura Dirt 1700m (Mile) Right Max Runners: 16
Oi Dirt 2000m (Medium) Right Max Runners: 16
Oi Dirt 1800m (Mile) Right Max Runners: 16
Oi Dirt 1200m (Sprint) Right Max Runners: 16
```

---

## 23. First prompt to Claude Code

Use this prompt after placing this file in the repository:

```text
You are helping rebuild the Uma Ladder project from scratch.

Read PROJECT_INTENTIONS.md fully before changing files.

First task:
1. Audit the existing repository structure.
2. Identify what should be reused conceptually and what should be discarded.
3. Recommend the final stack for this rebuild, defaulting to Flask app-factory + SQLAlchemy + migrations + Jinja/HTMX unless you find a strong reason to propose Django.
4. Do not implement the full app yet.
5. Create or update only planning/scaffolding documents needed for the next implementation PR.
6. Do not create branches.
7. Do not run git add.
8. Do not run git commit.
9. Do not add .claude files.
10. End with a concrete first implementation plan broken into small PR-sized steps.

When you propose implementation, prefer:
- small reviewable diffs,
- tests before complex behavior,
- no hardcoded secrets,
- no live scraping inside request handlers,
- service modules for business logic,
- route handlers kept thin.
```

---

## 24. First implementation branch suggestion

Human-run command:

```bash
git checkout -b rewrite/project-foundation
```

Suggested first PR scope:

- add this project intentions document,
- add architecture decision record for stack choice,
- add initial clean project skeleton only if stack is decided,
- add pytest/ruff configuration if missing,
- do not implement race logic yet.

