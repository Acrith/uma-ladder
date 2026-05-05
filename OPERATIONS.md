# Operations

Day-to-day knobs for running Uma Ladder. Source of truth for product
intent is `PROJECT_INTENTIONS.md` — this file documents what an
operator needs to *do*.

## OCR

OCR is opt-in. Without configuration the app uses the `manual` provider
which returns empty parses — every result must be typed in by hand.
Three providers ship in-tree:

| `OCR_PROVIDER` | When to use                          | Setup |
|----------------|--------------------------------------|-------|
| `manual`       | Default. No OCR; manual entry only.  | None. |
| `mock`         | Local UI dev / tests.                | None. |
| `google_vision`| Production.                          | API key (below). |

### Enabling Google Cloud Vision

1. In the GCP console, enable the **Cloud Vision API** for your
   project.
2. Create an API key (`APIs & Services → Credentials → Create
   credentials → API key`).
3. **Restrict the key**:
   - *API restrictions* → "Cloud Vision API" only.
   - *Application restrictions* → IP addresses → your server's egress
     IP. (We use the API key over a service-account JSON to keep
     deploys simple — one env var, no file mounts. The IP restriction
     is what makes that safe.)
4. Set environment variables before starting the app:

   ```bash
   export OCR_PROVIDER=google_vision
   export GOOGLE_VISION_API_KEY=AIza...
   ```

5. Smoke-test by uploading a result screenshot via
   `/official/<id>/results-screenshot` (organiser role required).

### What the parser produces

The `google_vision` provider populates three fields on every parse:

- **`rows`** — clustered placement rows for the result-summary screen
  (`{placement, uma_name, raw_line, confidence}`).
- **`stats`** — `{speed, stamina, power, guts, wisdom}` paired against
  the labels on a Uma stat screen. "Wit" is recognised as an alias for
  "Wisdom".
- **`skills`** — clustered lines that look like skill names (drops
  numeric rows, stat-label rows, and lines beginning with a placement
  digit). The downstream fuzzy matcher in `services/official` is the
  authority on what's a real skill — it strips typography
  differences (em-dash vs hyphen, ☆ vs missing-☆, etc.) before
  comparing to `UmaSkill.name_en`. Names with no match are still
  preserved as `raw_ocr_text` so an organiser can fix the catalogue
  or correct the spelling later.

### Cost shape

One Vision API request per uploaded screenshot (no per-feature
multiplier — we use only `DOCUMENT_TEXT_DETECTION`). Confirmation /
re-edits don't re-call the API.

## Discord notifications

Each notification target maps to a webhook URL via env var. Without
the URLs configured, attempts land in the `discord_notification_attempts`
table with `status=skipped` — the calling service still succeeds.

| Target name           | Env var                                     |
|-----------------------|---------------------------------------------|
| `race_registration`   | `DISCORD_WEBHOOK_RACE_REGISTRATION_URL`     |
| `official_results`    | `DISCORD_WEBHOOK_OFFICIAL_RESULTS_URL`      |
| `draft_results`       | `DISCORD_WEBHOOK_DRAFT_RESULTS_URL`         |
| `fallback`            | `DISCORD_WEBHOOK_FALLBACK_URL`              |

Lifecycle events: race published, room code, results, race cancelled,
registration removed, draft match cancelled. Failures are recorded in
`discord_notification_attempts`; admin UI at `/notifications` (admin
role) supports manual retry.

## Database migrations

Dev: `FLASK_APP=uma_ladder FLASK_CONFIG=development flask db upgrade`.

Production cutover: bring the app down (or run a maintenance window),
upgrade, bring it back. Migrations to date are all additive (new
columns / new tables / nullable FKs) so a rolling deploy is also safe
in practice — but explicit downtime is the simpler ops story.

## Refreshing GameTora seed data

GameTora updates the underlying JSON on a content-hash version every
few weeks. The fetchers are polite single-request pulls; commit the
snapshot then re-seed.

```bash
flask uma fetch-gametora-characters && flask uma seed-characters
flask uma fetch-gametora-outfits    && flask uma seed-outfits
flask uma fetch-gametora-g1-races   && flask uma import-g1-races
flask uma fetch-gametora-skills     && flask uma seed-skills
```

`--prune-missing` on each seeder disables rows that have dropped out
of the upstream snapshot rather than deleting them — Oshi/result
references stay intact.
