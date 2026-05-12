# Privacy policy

_Last updated: 2026-05-12_

Uma Ladder is a community-run ranking + tournament site for the
Umamusume Pretty Derby player community. We are not affiliated with
Cygames, Inc. This page tells you what we collect, why, who else
sees it, and how to ask us to delete it.

The site is operated by the **Uma Ladder maintainers** on a
not-for-profit, volunteer basis. We process personal data in line
with the EU General Data Protection Regulation (GDPR) regardless of
where you live, because it's the most protective baseline and we
don't want two policies.

## What we collect

When you create an account or use the service, we hold the
following data about you:

- **Account identity.** Username and a hashed password (we never
  see or store your plaintext password). Display name, bio, avatar,
  oshi, and friend code if you fill them in on your profile.
- **OAuth links.** If you sign in with Discord or Google, we store
  the provider's stable user ID, your handle / email on that
  provider, and your avatar URL — only what's needed to recognise
  you the next time you click "Continue with …".
- **Game activity.** Races you've entered, draft matches you've
  played, your placements, ELO rating, the umas you raced, skills
  recorded from screenshots, and the result-screen images uploaded
  by organizers.
- **Club affiliation.** If your friend code matches a player in a
  club tracked on uma.moe, we cache that club's ID and name so the
  site can show you on the right team's leaderboard.
- **IP address.** Used transiently for per-IP rate limiting on
  sensitive endpoints (signup, login, password reset, OCR upload).
  Kept in memory only — wiped when the server restarts. Each
  successful claim of an invite code records the IP it came from in
  the audit table.
- **Moderation history.** If a moderator takes action on your
  account (role change, account disable, account restore, report
  resolution), it's recorded in an admin audit log.
- **Reports.** If another user files a report against you, the
  reporter's username, the reason text, and the URL they were on
  when they filed are stored until an admin reviews them.
- **Session + CSRF cookies.** Set by your browser to keep you
  logged in and to protect form submissions from cross-site abuse.
  We do not run analytics, tracking pixels, or third-party
  advertising cookies.

## Why we collect it

- To run the service: track results, compute rankings, render
  player profiles.
- To prevent abuse: rate limiting, invite-only signup (currently
  enabled), report queue, and audit trail.
- To recognise you when you return: session cookies and OAuth IDs.

## Who else sees it

- **Other Uma Ladder players** see your username, display name,
  avatar, oshi, race history, draft match history, and ranking.
  They do not see your email or Discord handle unless you put it
  in your public profile yourself.
- **Fly.io** hosts the service. Standard HTTP request data passes
  through their infrastructure.
- **Discord** and **Google** see the OAuth dance when you click
  "Continue with …" — they receive their own client ID, not your
  Uma Ladder password.
- **uma.moe** receives your friend code when we look up your club
  affiliation. We don't send anything else.
- **Sentry** (only if the maintainers have configured a Sentry
  project) receives error reports. Personally identifiable
  information is disabled in that integration; what they get is
  the exception class, stack trace, and request path.
- **GameTora** is linked from our footer for race data attribution.
  We do not share data with them; clicking the link follows their
  privacy policy from then on.

We don't sell your data, share it with advertisers, or hand it to
anyone except as required by law.

## How long we keep it

- **Active accounts:** as long as the account exists.
- **Account deletion on request — soft delete is the default.**
  If you ask us to delete your account, we soft-delete it: your
  public profile is blanked (display name, oshi, avatar, friend
  code, Discord handle all cleared), your linked Discord / Google
  identities are dropped so a future owner of that Discord can't
  inherit your history, your password is scrambled so login is
  impossible, and you render as a partially-anonymized
  `K***e`-style mask everywhere you still appear in race / draft
  history. This preserves other players' rankings and match
  records while removing your personal identifiers — your account
  is no longer addressable, queryable, or distinguishable from
  any other deleted account from the outside.
- **Hard delete is an admin-only edge case.** Reserved for things
  like a fresh duplicate signup with no history, or a clear
  policy violation where soft-delete isn't appropriate. Hard
  delete cascades into race registrations, draft results, and
  other rows tied to the account, which can affect other players'
  visible history. We won't hard-delete a user-requested deletion
  unless you specifically ask for it AND the history loss is
  acceptable to you.
- **Rate-limit IP data:** in-memory only; lost on every restart.
- **Audit logs and report records:** indefinitely.

## Your rights under GDPR

You can ask us, at any time, to:

- **See** what we hold about you (Article 15);
- **Correct** anything inaccurate (Article 16);
- **Delete** your account and associated data (Article 17 —
  "right to be forgotten");
- **Export** a copy of your data in a machine-readable format
  (Article 20);
- **Object** to a specific use of your data (Article 21);
- **Withdraw consent** for OAuth links — you can unlink Discord
  or Google from `/profiles/edit` at any time.

We aim to respond within 30 days.

You can also lodge a complaint with your local data-protection
authority if you think we've handled your data badly. We'd rather
fix it ourselves first — see the contact below.

## Contact

For any privacy or data-protection question — including all the
GDPR rights above — email:

**r.krawczak@protonmail.com**

Use a subject line like "Uma Ladder — data request" so it doesn't
end up in spam.

## Changes to this policy

If we change how we handle data, we'll update the date at the top
of this page and post a short note in the
[What's new](/changelog) feed.
