# What's new

User-visible changes, newest first. Internal hardening (refactors,
test additions, dependency bumps) is not listed here — see the git
log for the full history.

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
