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

### Smoke-testing the configured provider

Before real races flow through the UI, verify the pipeline against
a local screenshot:

```bash
flask uma ocr-test ~/screenshots/result.png
flask uma ocr-test ~/screenshots/stats.png --provider google_vision
```

Reads the configured provider (or the `--provider` override), prints
parsed rows / stats / skills / confidence. Doesn't write to the DB,
so it's safe to run repeatedly. Use it to:

- Confirm the API key + IP restriction work after deploy.
- Compare `mock` vs `google_vision` against the same image to see
  what fields the parser is finding.
- Triage why a real screenshot didn't parse the way you expected
  before opening the UI flow.

### Troubleshooting

**`PERMISSION_DENIED: Cloud Vision API has not been used`** — enable
the API in the GCP console (`APIs & Services → Library → Cloud
Vision API → Enable`).

**`API key not valid`** — verify `GOOGLE_VISION_API_KEY` env var is
set and the key still exists in the GCP console. Cloud Run / Heroku
deploys: confirm the env var made it through the runtime, not just
the build step.

**`Requests from referer / IP blocked`** — your key's *Application
restrictions* don't include the server's egress IP. The key needs to
be reachable *from the server*, not from a browser; "HTTP referrers"
is the wrong restriction type for our use case.

**Empty / degenerate parses on a clear screenshot** — the
`fullTextAnnotation` block can come back missing if the image is too
small. Vision wants ≥ ~640px on the long side for reliable detection
on game UIs. Re-take the screenshot at native game resolution; don't
downscale.

**Image-too-large** — Vision rejects > 20 MB body (after base64
encoding, so ~15 MB raw). Stat screenshots from a phone may exceed
this. The OCR step page exposes the upstream `error_message` inline
so you'll see this clearly when it happens.

**Step page is blank after upload** — check
`/notifications` (admin role) for the OcrParseAttempt row by id; its
`error_message` and `raw_text` fields capture what Vision actually
returned. Or run `flask uma ocr-test` against the same screenshot
locally for a faster feedback loop.

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

## Backups + disaster recovery

What needs preserving:

| Data                 | Where                                          | Rebuildable? |
|----------------------|------------------------------------------------|--------------|
| Application DB       | `instance/uma_ladder.dev.sqlite` (dev) / Postgres (prod) | **No** — primary source of truth (users, matches, races, results, audit log, uma.moe cache). |
| User-uploaded files  | `instance/uploads/<uuid>` (avatars + OCR screenshots) | **No** — referenced by `UploadedImage.storage_key`; orphaned UploadedImage rows if files vanish. |
| GameTora seeds       | `data/seeds/*.json`                            | **Yes** — `flask uma fetch-gametora-*` regenerates from upstream. |
| Tailwind output      | `uma_ladder/static/css/output.css`             | **Yes** — `make tailwind`. |
| Build artifacts      | `.venv/`, `__pycache__/`                       | **Yes** — discard freely. |

So a backup must cover **the DB + `instance/uploads/`**. Everything
else can be rebuilt from git + the seed-fetcher commands.

### SQLite (dev / small-prod)

Use SQLite's built-in online backup — safe to run while the app is
serving requests, no app downtime required:

```bash
sqlite3 instance/uma_ladder.dev.sqlite ".backup '/var/backups/uma-ladder/$(date -u +%Y%m%dT%H%M).sqlite'"
```

Pair with a tarball of the uploads directory for a full snapshot:

```bash
tar czf "/var/backups/uma-ladder/uploads-$(date -u +%Y%m%dT%H%M).tar.gz" instance/uploads/
```

Schedule both via cron — daily is reasonable for a community ladder,
hourly if you want tighter RPO. Keep ~14 days of dailies + ~6
months of monthlies (rsync-style retention).

For continuous replication, **litestream** is the recommended
upgrade. It streams the SQLite WAL to S3 / Backblaze / Azure with
sub-minute RPO and zero downtime:

```yaml
# litestream.yml
dbs:
  - path: /app/instance/uma_ladder.dev.sqlite
    replicas:
      - type: s3
        bucket: uma-ladder-backups
        path: db
        region: <region>
        access-key-id: $AWS_ACCESS_KEY_ID
        secret-access-key: $AWS_SECRET_ACCESS_KEY
```

Run `litestream replicate -config litestream.yml` as a sidecar
process under your service manager (systemd / supervisord / Docker).
The uploads directory still needs separate periodic sync (rsync /
restic / `aws s3 sync`).

### Postgres (large-prod)

When you outgrow SQLite, the `DATABASE_URL` env var swap is the
only schema-side change required. Backup story changes to:

- Daily `pg_dump` (logical backup) → object storage.
- Continuous WAL archiving (`archive_command` / `pg_basebackup`) for
  point-in-time recovery (PITR).
- Managed-Postgres providers (RDS, Supabase, Neon) handle this
  automatically — point Uma Ladder at one and you can skip writing
  your own pipeline.

`instance/uploads/` migration: when moving off the same host as the
DB, swap the local-disk strategy for object storage (S3-compatible).
The `UploadedImage.storage_key` column already abstracts the path —
the only change is in `services/ocr.save_uploaded_image` and
`services.profiles.serve_avatar` to read/write via boto3 instead of
the local FS. Not in scope for now; flagged here so the backup story
covers the eventual migration.

### Restoring

SQLite restore is a file copy:

```bash
# 1. Stop the app.
systemctl stop uma-ladder

# 2. Replace the DB file.
cp /var/backups/uma-ladder/<timestamp>.sqlite instance/uma_ladder.dev.sqlite

# 3. Restore uploads.
tar xzf /var/backups/uma-ladder/uploads-<timestamp>.tar.gz -C ./

# 4. Start the app.
systemctl start uma-ladder
```

Litestream restore: `litestream restore -o instance/uma_ladder.dev.sqlite s3://uma-ladder-backups/db`.

### Verification

A backup nobody's tested isn't a backup. Verify quarterly:

1. Spin up a throwaway VM / container.
2. Restore the latest snapshot per the steps above.
3. `flask db current` should report the same head as production.
4. Boot the app (`flask run`) and load `/` + `/admin/`.
5. Spot-check a known-good user's profile and a known-good race
   detail page — they should render with their full data including
   avatar (uploads tarball intact) and any uma.moe cached card.

Document any drift in this file and fix the gap.
