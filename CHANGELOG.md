# What's new

User-visible changes, newest first. Internal hardening (refactors,
test additions, dependency bumps) is not listed here — see the git
log for the full history.

## 2026-05-18

### Behind the scenes

- **Uma-sheet OCR sandbox: sheet-specific extractor (PR-OCR2).**
  Replaces the race-result parser's stat / skill extractors on
  the sandbox surface only — the race-result flow is untouched.
  Stats now match positionally (5 labels on one row → 5 numbers
  on the next, index-aligned, fixing the bug that put the same
  number in every cell). Aptitudes parse across the three rows
  (Track / Distance / Style) with rank letters. Skills are
  resolved by scanning the post-"Skills" text block against the
  UmaSkill catalogue, so multiple skills merged on one row (or
  decorated with "Lvl N" / circle markers) still resolve. A
  known limitation: when Vision splits a multi-word skill across
  rows AND injects another skill between the halves (the
  "White Lightning Comin' Through!" case), the contiguous
  substring matcher can't reassemble it — captured as a regression
  test so a future fix can flip the assertion. Header card now
  surfaces uma name, outfit, epithet, score, and trainer name
  when present.
- **Uma-sheet OCR sandbox (admin).** A new admin-only iteration
  bench at **OCR → Uma sheet sandbox** lets a maintainer upload an
  in-game Uma profile / character-sheet screenshot and see what
  the OCR pipeline extracts — raw text, structured stats panel,
  matched-vs-unmatched skill candidates against the `UmaSkill`
  catalogue, and any other detected text clusters that didn't fit
  a known field. Read-only: nothing is saved to any race or
  profile. Purpose is iteration — we'll use it to figure out what
  sheet-specific extraction heuristics the parser needs before
  wiring the data anywhere user-facing. Regular users won't see
  the tile.

## 2026-05-12

### Legal

- **Privacy policy + GDPR contact.** A new
  [Privacy](/privacy) page (linked from the footer) explains what
  Uma Ladder collects (account fields, OAuth identities, race /
  draft history, IP for rate limiting), who it's shared with
  (Fly.io as host, Discord / Google for OAuth, uma.moe for club
  sync, Sentry if configured), and how to exercise your GDPR
  rights. **Deletion-on-request is soft-delete by default** —
  your public profile is blanked, OAuth links are dropped, and
  you render as a `K***e`-style mask everywhere you still appear
  in race / draft history, so the records other players rely on
  stay intact. Hard delete is reserved for admin edge cases and
  isn't applied to a user-deletion request unless you specifically
  ask for it. Contact for any data request:
  **r.krawczak@protonmail.com**. Source lives at `PRIVACY.md` in
  the repo so edits go through the same review path as code.

### Anti-spam

- **Invite-only signup gate.** A new admin page at
  **Admin → Invite codes** lets a moderator mint invite codes
  (`XXXX-XXXX-XXXX`, drawn from a no-ambiguous-glyphs alphabet) and
  flip a single switch to require one on every new signup. The gate
  ships **OFF by default** — landing this update changes nothing
  until an admin enables it, so you can mint a batch of codes,
  decide who to send them to, then turn the toggle on when the
  semi-closed beta starts. Existing accounts always log in
  unaffected: password reset, OAuth-link-to-existing, and ordinary
  login skip the gate. When it's on, the sign-up page shows a
  single invite-code field above the *Continue with Discord /
  Google* buttons and the password form — the same code applies to
  every signup path. Codes can be **single-use** (default), shared
  with a custom max-uses count (e.g. one code for a 25-person
  club), or generated in a **bulk batch** so each invitee gets
  their own auditable token. Each code carries an optional label
  ("InyanyaCup club bulk," "DM @kezuke," etc.) so you can audit
  who got what later, and a one-click **Revoke** kills any code
  that leaks. All flip / mint / revoke actions are audit-logged.

### Official races

- **Cleaner race detail header + live countdown.** The race detail
  page used to fan out every detail as a chip row — track, season,
  scheduled time, weather, ground, plus three separate
  visibility-switch buttons and the cancel/delete buttons all
  competing for attention next to *Open registration*. Track info
  (preset name, surface, distance, direction, course variant) and
  conditions (race season, weather, ground) now live in a dedicated
  amber-bordered **Track** card directly below the title, matching
  the layout Draft matches already use. *Make public / private /
  club-only* collapsed into a single **Visibility ▾** dropdown that
  shows the current state at a glance, and *Cancel race* +
  *Delete race* hide inside an **Actions ▾** dropdown so the eye
  goes straight to the primary CTA. The scheduled time now renders
  in **your browser's local timezone** with a live *"— starts in
  5d 14h"* tail that updates every 30 seconds (and flips to
  *"started X ago"* once the race begins), so you no longer have to
  do the UTC math in your head.

## 2026-05-11

### Achievements

- **Badges unlock automatically.** Until now the milestone
  achievements (*First Official Race*, *First Official Podium*,
  *First Official Win*, *First Draft Match*, *First Draft Win*,
  *Season Podium*, *Season Champion*) only landed via admin manual
  grant. They now grant automatically the moment the underlying
  event happens — submitting a race result, completing a draft
  match, or flipping a season to **Completed**. Grants are
  idempotent (re-submitting or editing a result doesn't multi-
  award) and fail-safe (an achievement bug can't break the result
  save or season transition). Manual admin grant stays available
  for the rest of the catalogue (*Founding Member*, etc.) and for
  retroactive awards.

### Safety

- **Report this user.** A new collapsed *Report this user* control
  on each player profile (visible only to logged-in viewers looking
  at someone else's profile) lets the community flag bad behaviour
  for admin review. Reports include a free-text reason (capped at
  500 characters) and the URL the report was filed from so an admin
  can jump straight to the context. The new **Admin → Reports**
  queue lists open reports newest first; each row shows a
  credibility chip (e.g. *3 prior dismissed*) when the reporter has
  previously had reports dismissed, so admins can weigh suspicious
  reporting patterns at a glance. Resolving a report is a one-click
  *Mark actioned* or *Dismiss* with an optional notes field for the
  next admin reviewing the log. Reports are rate-limited at 5 per
  hour per IP to keep the queue scannable; all transitions are
  audit-logged.

### Admin

- **Disable account (soft delete).** A new **Moderation** card on
  the admin user detail page lets a superadmin disable an account
  without nuking its history. Disabling blanks the public profile
  (display name, oshi, avatar, friend code, Discord handle), drops
  linked OAuth identities so a legitimate-original user can re-link
  Discord/Google to their real account, scrambles the password so
  login is impossible, and hides the user from the Players index +
  Rankings tabs. **Past race results, draft matches, and opponent
  ELO chains stay intact** — the disabled user just renders as
  `K***e` everywhere they still appear in history. A
  **Restore account** button replaces the disable card while the
  account is in the disabled state; restoring re-enables login but
  leaves the blanked profile alone (the user fills it back in
  themselves once the admin issues a password reset link). The
  existing Danger zone (hard delete) stays available for empty
  duplicates with no history; for active racers, disable is the
  right tool. Audit-logged under `user_disable` / `user_restore`.

### Performance

- **Firefox: lower GPU usage from the dark UI.** Firefox-family
  browsers (Firefox, Zen, LibreWolf, etc.) draw the site's frosted
  cards and blurred background glows on the GPU's slow path,
  sustaining 40-50% GPU on some Linux setups just from scrolling.
  We now detect Firefox at page load and disable the backdrop blur
  + reduce the decorative blur halos for that engine only — the
  page still has its dark cards and colour washes, just without
  the heavy filter. Chrome / Safari / Edge users see no change.

### Safety

- **Per-IP rate limiting on sensitive endpoints.** Registration,
  login, password-reset request, OAuth callbacks (Discord +
  Google), profile updates, avatar removal, and every OCR /
  screenshot upload route now have per-IP rate limits sized for
  legitimate use. A flood of attempts (e.g. mass-account creation
  or rapid OCR upload) hits a friendly **Too many requests** page
  with the limit and "try again later" instead of consuming
  resources. Login already had per-username lockout (PR-J10); this
  adds the per-IP cap so the two layers compose. In-memory
  counters reset on app restart; we'll move to a shared backend
  when we scale beyond one Fly machine.

### Admin

- **Delete user from the admin panel.** A new **Danger zone** card
  on the admin user detail page lets a superadmin permanently remove
  an account — useful for cleaning up duplicates created when
  someone clicks "Continue with Discord" instead of logging in.
  Type the username to confirm; the deletion sweeps profile, linked
  OAuth identities, draft match participations, race registrations,
  inbox notifications and achievements. Past *race results* (saved
  placings) survive as `@?` so leaderboards and history stay
  consistent. Audit-logged. Superadmin-only for now; admin-level
  access can be granted later by relaxing the route gate.

### Official races

- **Display names on the registration list.** The Registrations card
  and the manual results entry form on a race detail page now show
  each player's display name (proper-cased, may contain spaces)
  instead of their lowercased storage username. Same pattern the
  Players index and Rankings table already use.

## 2026-05-10

### Sign-in

- **Continue with Google.** A second OAuth provider, sitting next
  to the Discord button on the login and register pages and as a
  new row in the **Linked accounts** card on your profile. First-
  time use creates a fresh Uma Ladder account from your Google
  display name (with a short suffix if it's already taken);
  subsequent visits log you straight in. We request only your
  Google ID and display name — no email, no contacts, no Drive.

### Clubs

- **Club pages.** A new `/clubs/<id>` page lists every Uma Ladder
  member of a uma.moe club. Click any club name from a player's
  profile or a Club-only race chip to land on it. The page links
  out to uma.moe for the deep stats view; the roster + (eventually)
  per-club ladder live on Uma Ladder. Member rows reuse the same
  card style as the Players index — avatar, display name,
  username, oshi.

### Rankings

- **Unified Rankings page.** A new top-nav surface at `/rankings`
  aggregates Official + Draft season ladders behind a tabbed UI
  with a season picker. The dashboard "Full →" links and the
  `/official/ladder/<id>` + `/draft/ladder/<id>` URLs all redirect
  here, so old Discord screenshots and bookmarks still work.
  Player rows now show an avatar + display name + oshi (same row
  style as Players + Clubs), making it easier to recognize
  someone you've raced against without having to read the
  username carefully.
- **My-club scope filter on Rankings.** A small toggle next to
  the Official / Draft tabs lets you flip the ladder between
  *All players* and *My club* — see exactly where you stand
  among your clubmates without scrolling through the full
  community. Visible only when you're signed in and your friend
  code links you to a club; works on both the Official and
  Draft tabs and preserves the season + page across switches.

### Targeted matches

- **Multi-club allowlist on Club-only races.** Organizers running
  an allied-club tournament can now add additional clubs to a
  Club-visibility race. The race detail page surfaces an
  **Allowed clubs** card with your own club locked at the top and
  a quick-add picker showing every club Uma Ladder has seen, plus
  an input to paste any uma.moe club ID by hand. Members of any
  listed club can see and register; the existing per-user
  invitee list stays as the override for one-off out-of-club
  guests.

### Clubs

- **Total member count on club pages.** The `/clubs/<id>` header
  now shows "X of Y members on Uma Ladder" so visitors understand
  the roster is partial — Y is what uma.moe reports for the
  whole club, X is how many of those have linked their friend
  code on Uma Ladder. The number was already in the trainer JSON
  we fetch for in-game stats, so this required no additional
  uma.moe API calls; it just wasn't being captured before.

### Profile

- **Avatar border tone picker.** Pick a border tone for your
  avatar from a 12-color palette (cyan, fuchsia, emerald, amber,
  rose, violet, sky, indigo, lime, orange, pink, slate). The
  ring shows up everywhere your avatar appears: profile hero,
  Players index, Clubs roster, Rankings table. Picker is a
  visual swatch grid — the colors are the colors. Your oshi
  gives you a default fuchsia ring; pick any other tone to
  override it (selecting *Default* falls back to the oshi tone).
  Foundation for future earnable special borders from seasons,
  tournaments, and trophies.

- **Achievements.** Earned badges now appear on your public
  profile. Linking your Discord and Google accounts grants the
  matching badge instantly. Other badges (Founding Member,
  First Race, First Win, Season Champion, etc.) are grantable
  by admins for now — auto-grant logic for race results and
  season closeouts will arrive in follow-up updates. Locked /
  unearned badges don't render here so profiles stay clean;
  visit any active player's profile to see what others have
  earned.
- **Achievement icons.** Switched the seed catalogue from emoji
  glyphs to stroke SVG icons (matching the rest of the site's
  icon system). Same visual storytelling — trophy, crown, flag,
  swords, etc. — without the emoji-rendering inconsistency
  across browsers. Custom designed artwork is queued for the
  longer term.

### Profile

- **Profile tabs.** Player profiles now have three tabs:
  **Overview** (the rich oshi hero with stat tiles + in-game
  stats from uma.moe + an achievements snippet), **Matches**
  (`/profiles/<u>/history` — Recent Official + Recent Draft +
  Most-used + Track strengths), and **Achievements**
  (`/profiles/<u>/achievements` — full catalogue with locked
  entries grayed-out + how-to-earn tooltips). The big hero
  shows only on Overview; the other tabs get a slim tab strip
  so they breathe for content. When you're viewing someone
  else's Matches or Achievements, a "Viewing: [avatar +
  name + @username]" indicator sits on the right of the tab
  strip so you don't lose track of whose data you're reading.
  Old single-page URL still works — it's the Overview tab.
- **Rich self-identity in the top nav.** The top-right
  username link is now a chip showing your avatar + display
  name + @username (the same format the old compact profile
  header used). Drops you onto your own profile on click,
  same as before — but at a glance you see your own setup
  reflected back. Hides the text on narrow screens to keep
  mobile nav compact.

- **Achievement showcase on the hero.** Pin up to 6
  achievements to your Overview hero. They render as tier-
  colored badge tiles (gold gets a glow) at the bottom of the
  hero's left column — picture a small trophy display case.
  Drag-and-drop picker on the profile editor: drag any
  unlocked achievement into the "Pinned" zone to feature it,
  drag back into the pool to unpin, drag within the pinned
  zone to reorder. Click any badge on the hero to jump to
  the full Achievements tab. When custom badge artwork lands
  in a future update, the same tiles will render the artwork
  in place of the icon.

## 2026-05-09

### Sign-in

- **Continue with Discord.** A new button on the login and register
  pages signs you in with your Discord account. First-time use
  creates a fresh Uma Ladder account with your Discord username
  (with a short suffix if it's already taken); subsequent visits
  log you straight in. If you already have a Uma Ladder account,
  log in normally first and clicking "Continue with Discord" links
  the two — your Discord ID becomes the verified source for
  notification pings, replacing the manual handle field.
- **Link / unlink Discord on the profile editor.** A new
  "Linked accounts" card on `/profiles/me` shows whether your
  Discord is linked and lets you connect or disconnect it
  without going through the login page.
- **Verified badge on public profiles.** The Discord chip on a
  player's public profile now shows a bright cyan ✓ when the
  account is OAuth-verified (vs the existing soft `@` glyph
  for manually-entered handles). Hovering each glyph explains
  what it means.

### Quality of life

- **Cleaner top nav.** *Race Presets*, *OCR*, and *Skill list*
  moved into a single **Tools** dropdown — less clutter for
  players who never touch them, still one click away. Top
  row is now Home · Official · Draft · Players · Tools.
- **Inbox bell signals unread at a glance.** When you have
  unread items, the bell icon now glows fuchsia (matching the
  count badge) so you don't have to read the small digit to
  notice. Returns to neutral once you've cleared inbox.

### Bug fixes

- **Dashboard upcoming card now shows your private + club races.**
  The "Official · upcoming" card on the home page was filtering
  by Public-only, so users invited to a Private race or in a
  Club race didn't see it from the dashboard. Fixed.

### Targeted matches

- **Club-only official races.** When creating an official race,
  organizers can now mark it **Club only** in addition to
  Public / Private. The race is visible only to members of the
  organizer's uma.moe club (synced from your friend code). The
  organizer can still hand-pick non-members via the existing
  invitee list — useful for inviting an out-of-club coach or
  friend without opening the race fully. Switching an existing
  race to Club-only auto-promotes any current registrants who
  aren't in the club into the invitee list, so nobody loses
  access to a race they already joined.

## 2026-05-08

### Quality of life

- **Targeted Official Matches.** When creating an official race,
  organizers can now mark it as **Private** — only the organizer
  and explicitly invited users see it on the index or can register.
  Manage invitees on the race detail page after creating it.
  Default stays Public.
- **Race visibility can be changed after creation.** Switching a
  race from Public to Private auto-adds everyone already
  registered to the invitee list, so nobody loses access to a race
  they already joined. Switching the other way just opens the gate.
- **In-app inbox.** A bell icon in the navbar shows when you have
  notifications waiting. Draft invites surface here automatically
  (no more discovering them only by visiting the Draft page).
  Clicking the bell marks them read; accept / decline / cancel
  cleans them up.
- **Admin seasons status dropdown fix.** The status changer on
  the admin Seasons list no longer clips inside the table — uses
  a native browser dropdown that escapes the layout boundary.
  Admin-only, so most users won't notice; admins definitely will.

## 2026-05-07

### Quality of life

- **Profile clubs link to uma.moe.** The Club tile on a public
  profile now opens the club's uma.moe page in a new tab.
- **Track-ban panel layout fixed.** Distance and Direction bans
  no longer wrap onto two lines and truncate the uma-ban row
  beside them.
- **Lobby polling no longer wipes ban selections.** Mid-edit
  selections during the track-ban and uma-ban phases survive the
  5-second auto-refresh — the second player to ban won't lose
  their choice while the page polls for the opponent.
- **OCR screenshots stay visible after the match.** Source
  screenshots that were OCR-parsed for a draft match are now
  shown on the completed-match card. Both participants and senior
  organizers can view them; previously only the uploader could.

### Game-rule accuracy

- **Weather × Ground combinations match in-game rules.** Only the
  eight valid combos are accepted (Sunny/Cloudy with Firm/Good,
  Rainy with Soft/Heavy, Snowy with Good/Soft and Winter only).
  The random roll for draft matches respects these too.

### Fairness

- **Uma ban is now truly blind.** Until both players submit, neither
  can see the opponent's pick — not in the side panel, not as a
  grayed tile in the picker. Reveal happens automatically once both
  bans land.

### Permissions and safety

- **Ownership gate on official-race actions.** Organizers can only
  open / close / set-room-code / submit-results / cancel races they
  themselves created. Senior organizers and admins keep moderation
  override.
- **Security cleanup.** Login `next` and post-action redirects now
  reject external URLs (closes a phishing vector). Request body
  size is capped at the WSGI layer. Production cookies are marked
  Secure / HttpOnly / SameSite=Lax.
- **Login lockout against brute force.** After 10 wrong-password
  attempts in a row, the account is locked for 15 minutes — the
  form tells you how long to wait. A correct password at any point
  resets the counter.
- **Admin-issued password resets.** If you forget your password,
  reach out to an admin via Discord — they can generate a one-time
  reset link for you to set a new password. Self-service email
  reset will follow once Discord / Google sign-in is added.
