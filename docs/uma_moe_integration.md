# uma.moe integration — recon notes + integration plan

Reference for piggy-backing on uma.moe's third-party Umamusume profile
API. Captured from a polite frontend-bundle audit + a handful of GET
probes (single requests, identifying User-Agent, no scanning). Source
of truth for API shape until we cut over to an official Cygames API
(if that ever happens) or uma.moe ships a v5.

## Why uma.moe at all

Cygames doesn't publish a public Umamusume API. uma.moe is a popular
community tool that calls the game's own "view another player's
profile" endpoint server-side and exposes the result via a clean REST
JSON API. We don't reverse-engineer Cygames; we read uma.moe's response.

Risks:

- uma.moe could rate-limit, change shape, or shut down without notice.
  v4 in the path means they've broken it three times before. Treat as
  best-effort enrichment, never a primary data source.
- Cygames could ask uma.moe to take down the relevant endpoint;
  everything we depend on goes with it.

Polite-citizen practices we adopt:

- Identifying `User-Agent` header (`uma-ladder/<version> (+repo URL)`).
- Aggressive caching (12–24 h TTL by default).
- Single-flight per cache miss; never background polling.
- Graceful degradation — page renders the cosmetic card empty when
  the upstream is unavailable.

## API map (verified surface)

Confirmed via direct probe + frontend chunk inspection. Last verified
2026-05-05.

### v4 cluster

```
GET /api/v4/user/profile/<viewer_id>     — full trainer JSON (~19KB)
GET /api/v4/rankings/monthly?page=…      — paginated monthly fan-gain rankings
GET /api/v4/rankings/alltime             — all-time leaderboard
GET /api/v4/rankings/gains               — recent fan gains
```

Error format: `{"error": "<message>", "status": <code>}` for invalid
trainer ids. Non-routed paths return bare 404 (Cloudflare layer).

### Authentication (added 2026-05-06)

The operator published a Swagger UI at
[uma.moe/api/docs](https://uma.moe/api/docs) and confirmed the auth
shape: pass `X-API-Key: <key>` on every request. **Optional today**
(usage tracking only); the operator signalled it'll become required
at some future cutover.

Configure via `UMA_MOE_API_KEY` env var. `services/uma_moe.UrllibUmaMoeTransport`
attaches the header automatically when the key is set; omits it
when unset. On Fly the secret is set via
`fly secrets set UMA_MOE_API_KEY="..."`.

### v3 cluster (legacy, still operational)

```
GET  /api/v3/health
GET  /api/v3/search
GET  /api/v3/alltime
GET  /api/v3/monthly
GET  /api/v3/gains
GET  /api/v3/inheritance
GET  /api/v3/inheritance/<id>
POST /api/v3/inheritance/<id>/vote
GET  /api/v3/inheritance/<id>/friendlist_full
GET  /api/v3/stats /stats/today /stats/daily /stats/daily-visit
GET  /api/v3/support-cards/search
POST /api/v3/support-cards/submit
GET  /api/v3/support-cards/user/<id>
GET  /api/v3/support-cards/record/<id>
POST /api/v3/support-cards/record/<id>/vote
POST /api/v3/tasks/report-unavailable/<id>
POST /api/v3/tasks/track-copy/<id>
GET  /api/v3/tasks/trainer/<id>/status
```

### auth cluster (uma.moe's own user system — not relevant for our use)

```
GET  /api/auth/me
POST /api/auth/verify
GET  /api/auth/login/<provider>?origin=…
GET  /api/auth/identities
GET  /api/auth/api-keys           — uma.moe issues API keys to logged-in users
*    /api/auth/api-keys/<key>
GET  /api/auth/accounts           — `linkedAccounts` with `verification_status`
POST /api/auth/link
*    /api/auth/link/<id>
*    /api/auth/connect/<id>
*    /api/auth/disconnect/<id>
```

uma.moe's own verification flow: the user proves they own a
viewer_id by setting a generated token in their in-game `comment`
field. uma.moe reads it back via the public profile endpoint and
flips `verification_status` to `verified`. We can copy this pattern
when we need it.

### submission

```
POST /ingest/  — accepts user-submitted captures (schema not probed)
```

## v4 profile response shape

Single GET to `/api/v4/user/profile/<viewer_id>` returns:

```
trainer:                  account_id, name, follower_num, team_class,
                          best_team_class, team_evaluation_point,
                          rank_score, comment, leader_chara_dress_id,
                          release_num_info {act_num, card_num, ...},
                          trophy_num_info {grade_1, grade_2, grade_3, grade_ex},
                          team_stadium_user {best_point, ...},
                          own_follow_num, enable_circle_scout

circle:                   circle_id, name, member_count,
                          monthly_rank, monthly_point,
                          last_month_rank, last_month_point

circle_history[]:         year, month, circle_id, circle_name,
                          circle_rank, circle_points

fan_history.monthly[]:    viewer_id, trainer_name, year, month,
                          total_fans, monthly_gain, active_days,
                          avg_daily, avg_3d, avg_7d, avg_monthly,
                          rank, circle_id, circle_name

fan_history.rolling:      gain_3d, gain_7d, gain_30d,
                          rank_3d, rank_7d, rank_30d,
                          circle_id, circle_name

fan_history.alltime:      total_fans, total_gain, active_days,
                          avg_day, avg_week, avg_month,
                          rank, rank_total_fans, rank_total_gain,
                          rank_avg_day, rank_avg_week, rank_avg_month,
                          circle_id, circle_name

inheritance:              full breeding card — main/left/right parents,
                          blue/pink/green/white sparks, win saddles,
                          race results, factor counts

support_card:             featured card — id, limit_break_count, experience

team_stadium[]:           up to 15 trained Umas — each has
                          speed/stamina/power/wiz/guts, skill ids,
                          factor ids, support_card ids, aptitude grades,
                          rarity, talent_level, team_rating, scenario_id

veterans[]:               older retired showcase Umas (often empty)
```

## Integration plan (MVP scope)

Tight scope by user direction (2026-05-05). Pull just five fields,
render as a small cosmetic card. Defer everything else until uma.moe
API stability is proven (~6 months of v4 not breaking).

### Fields we use

```
circle.name                       → "Club"
fan_history.alltime.total_fans    → "Total fans"
fan_history.rolling.gain_7d       → "+7d"
fan_history.rolling.gain_30d      → "+30d"
fan_history.alltime.rank          → "Global rank"
```

### Implementation outline

Reuses existing `UserProfile.friend_code` as the lookup key —
12-digit friend codes ARE viewer_ids. No schema change for the ID.

```
migration:    uma_moe_cache (friend_code PK, fetched_at, payload_json,
              status). Separate from user_profiles so cache flush
              doesn't touch user data.

service:      services/uma_moe.py
              - UmaMoeTransport ABC + UrllibUmaMoeTransport
                (User-Agent + 10s timeout) + FakeUmaMoeTransport
                for tests.
              - fetch_trainer_summary(friend_code, *, max_age_hours=12)
                — cache check, calls /api/v4/user/profile/<id>, stores
                payload, returns TrainerSummary dataclass with the 5
                fields above. Returns None on 404/network/malformed —
                never raises into the caller.

config:       UMA_MOE_BASE_URL (default "https://uma.moe")
              UMA_MOE_CACHE_TTL_HOURS (default 12)

template:     /profiles/<u> renders an "In-game stats · via uma.moe"
              card when friend_code is set and cache resolves.
              Cached-at timestamp + small attribution.

tests:        FakeUmaMoeTransport-based:
              - happy path with captured fixture
              - 404
              - malformed JSON
              - cache hit within TTL (no extra transport call)
              - expired cache refetch
```

### What we explicitly skip in MVP

- **Verification flow.** Low-impact field set; if someone claims
  someone else's friend code the worst case is a wrong fan count
  on their profile.
- **Auto-fetch / background polling.** On-demand only, behind cache.

## Deferred features (do not lose)

Pending uma.moe API stability confirmation (≥6 months of v4 not
breaking) and/or project growth justifying the build cost.

1. **Team Stadium showcase.** The same v4 profile call already
   returns 15 fully-detailed trained Umas. We own the catalogues
   (`UmaSkill`, `UmaCharacter`, `UmaOutfit` keyed on `gametora_id`),
   so all skill/character/factor IDs in the response resolve
   *locally* with zero extra network calls. Renders as a 5-column
   card grid using the same chip styling we already ship for
   OCR-matched skills (PR20). High visual payoff once we trust the
   API will stick around.

2. **Global rankings card on dashboard.** `/api/v4/rankings/monthly`,
   `/rankings/alltime`, `/rankings/gains`. Render as a "Global top
   fan-gainers" card next to our seasonal Official + Elo top-5s. Tiny
   cache + tiny render. Doesn't change our ladder math; pure flair.

3. **OCR cross-validation against team_stadium.** When PR19b parses a
   stat-screen screenshot for a result and the user has a linked
   friend code, cross-reference `team_stadium` for that user's known
   stats. Discrepancy → soft "this doesn't match your latest team"
   warning. Catches OCR mistakes and discourages screenshot-doctoring.
   Doesn't block submission.

4. **Inheritance display.** Profile JSON includes the player's full
   breeding card — main/left/right parents with all sparks, factors,
   win saddles. Niche. Decoded master tables for spark/factor IDs
   would need to be sourced separately. Skip unless the community
   actively asks for it.

5. **Fan trend sparkline.** `fan_history.monthly[]` is six pre-aggregated
   data points. Cosmetic.

6. **Long-term aspiration: official Cygames API access.** If Uma
   Ladder grows into a recognised community tool, pursue an
   acknowledged data partnership instead of relying on uma.moe
   indefinitely. uma.moe's existence proves Cygames tolerates
   community data tools; the ask is realistic at scale.

## Operational notes

- **Cache table** lets us purge/refetch without `UserProfile` churn
  and naturally dedupes if two users somehow paste the same friend
  code.
- **No verification flow** in MVP. Add one (mirroring uma.moe's own
  in-game `comment` token pattern) only when we expand into fields
  where impersonation would hurt.
- **Failure mode** must be silent: return None, render empty card,
  log a `DEBUG`-level note. Never propagate exceptions into request
  handlers.
- **Rip-out plan.** When uma.moe v4 → v5 (or shuts down), the only
  surfaces that change are `services/uma_moe.py` + the cache table.
  Templates and routes don't need to know.
