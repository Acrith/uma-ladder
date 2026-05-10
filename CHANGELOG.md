# What's new

User-visible changes, newest first. Internal hardening (refactors,
test additions, dependency bumps) is not listed here — see the git
log for the full history.

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
