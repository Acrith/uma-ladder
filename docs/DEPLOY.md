# Deploying Uma Ladder to Fly.io

This guide bootstraps a fresh deployment from a clean Fly.io account.
Production refuses to boot without the required secrets, so the order
below matters: provision the database, set secrets, then deploy.

---

## 0. Prerequisites

- Fly.io account, payment method on file (the free tier covers a
  development-grade deploy).
- `flyctl` installed locally (`brew install flyctl` / `curl -L https://fly.io/install.sh | sh`).
- A Google Cloud Platform API key for Vision (only if you plan to use
  `OCR_PROVIDER=google_vision` — manual still works without it).
- Discord webhook URLs for the channels you want notifications in
  (optional; missing URLs cause the corresponding events to land as
  `status=skipped` in the audit log).

---

## 1. First-time provisioning

Pick an app name (lowercase, hyphens). Replace `<your-app>` below with
that name.

```bash
fly auth login
fly launch --no-deploy --copy-config --name <your-app> --org personal
fly postgres create --name <your-app>-db --region fra
fly postgres attach --app <your-app> <your-app>-db
```

`fly postgres attach` writes a `DATABASE_URL` secret into the app
automatically. Confirm:

```bash
fly secrets list --app <your-app>
```

You should see `DATABASE_URL` listed.

---

## 2. Required secrets

```bash
fly secrets set --app <your-app> \
  SECRET_KEY=$(openssl rand -hex 32)
```

`SECRET_KEY` is mandatory; the app refuses to boot without it.

---

## 3. Optional secrets

Only set the ones you actually want to enable. Missing values are
treated as "feature off."

```bash
# OCR
fly secrets set --app <your-app> \
  OCR_PROVIDER=google_vision \
  GOOGLE_VISION_API_KEY=AIza-your-key

# Discord webhooks (any subset)
fly secrets set --app <your-app> \
  DISCORD_WEBHOOK_RACE_REGISTRATION_URL=https://discord.com/api/webhooks/... \
  DISCORD_WEBHOOK_OFFICIAL_RESULTS_URL=https://discord.com/api/webhooks/... \
  DISCORD_WEBHOOK_DRAFT_RESULTS_URL=https://discord.com/api/webhooks/...
```

---

## 4. Deploy

```bash
fly deploy --app <your-app>
```

The container's `CMD` runs `flask db upgrade` before starting Gunicorn,
so migrations apply automatically on every deploy. Tailwind is built
into `uma_ladder/static/css/output.css` during the Docker image build —
no Node tooling at runtime.

After the deploy completes, hit:

```
https://<your-app>.fly.dev/healthz   # → {"status": "ok"}
https://<your-app>.fly.dev/          # dashboard
```

---

## 5. Seeding production data

Open a shell on the running machine and run the same CLIs you use in
dev:

```bash
fly ssh console --app <your-app>
flask uma seed-characters
flask uma seed-presets
exit
```

To create the first season, drop into the Flask shell:

```bash
fly ssh console --app <your-app>
flask shell
>>> from datetime import datetime, timezone, timedelta
>>> from uma_ladder.services.seasons import create_season
>>> now = datetime.now(timezone.utc)
>>> create_season(name="2026 Spring", starts_at=now, ends_at=now + timedelta(days=90))
>>> exit()
```

Promote a user to admin/organizer/etc. the same way:

```bash
flask shell
>>> from uma_ladder.extensions import db
>>> from uma_ladder.models import User, Role
>>> u = db.session.query(User).filter_by(username="you").one()
>>> u.role = Role.ADMIN
>>> db.session.commit()
```

---

## 6. Persistent storage

`fly.toml` mounts a volume named `uma_ladder_uploads` at `/app/instance`,
which is where uploaded screenshots live (`instance/uploads/`). Create
it before the first deploy if `fly launch` didn't:

```bash
fly volumes create uma_ladder_uploads --app <your-app> --region fra --size 1
```

PostgreSQL is the source of truth for everything else; the SQLite dev
DB is not used in production.

---

## 7. Updating

```bash
git push                       # via GitHub or wherever
fly deploy --app <your-app>    # rebuilds image, runs migrations, restarts
```

Rollback if needed:

```bash
fly releases --app <your-app>
fly releases rollback <version> --app <your-app>
```

---

## 8. Troubleshooting

- **App returns 500 on `/`**: check `fly logs --app <your-app>`. Most
  common cause is a missing `SECRET_KEY` or `DATABASE_URL`.
- **Migration failure on deploy**: `fly ssh console --app <your-app>`,
  then `flask db current` and `flask db upgrade`. The Dockerfile runs
  upgrade at boot so a stuck migration crashes the container.
- **Discord notifications all skipped**: check that the relevant
  `DISCORD_WEBHOOK_*_URL` secret is set. View the audit log at
  `/notifications/` (admin only).
- **Vision API rejects requests**: confirm the API key is enabled for
  Cloud Vision in the GCP console and any IP allowlist permits the
  Fly egress IPs (or remove the IP restriction during shake-out).
