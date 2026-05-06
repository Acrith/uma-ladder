# Permissions

Canonical reference for what each role can do. The codebase enforces
this via two layers:

1. **Role floor** — `min_role_required(Role.X)` decorator at the
   route level rejects users whose role rank is below `X`. Coarse;
   anyone above the floor is allowed in.
2. **Ownership** — `services/permissions.assert_can_act_on_race(...)`
   adds fine-grained gating: an organizer can only act on races
   they CREATED. Senior organizers and admins skip the ownership
   check — they're moderators-of-organizers.

Both layers compose: routes set the floor, services enforce
ownership. Calling a service directly (e.g. from a script or test)
still runs the ownership check.

## Roles, ranked

| Rank | Role | What they're for |
|------|------|------------------|
| 0 | `user` | Default. Plays draft matches, registers for official races, edits their own profile. |
| 1 | `organizer` | Runs their own official races (create, open, room-code, results, cancel). Has no authority on other organizers' races. |
| 2 | `senior_organizer` | Moderator of organizers. Can act on ANY official race regardless of who created it. Can't hard-delete (admin-only). |
| 3 | `admin` | Full moderation including hard delete. Can rewrite role assignments below admin. |
| 4 | `superadmin` | Full control. Can promote/demote admin and other superadmins. |

Promote a user via `/admin/users/<id>/role` (admin role required).
Bootstrap the first superadmin manually in the DB (see
[OPERATIONS.md](../OPERATIONS.md) — admin assignment section).

## Permission matrix

### Profiles + auth

| Action | Min role | Ownership |
|---|---|---|
| Browse `/profiles/` | none | — |
| View public profile | none | — |
| Edit own profile | `user`+ | self only |

### Official races

| Action | Min role | Ownership |
|---|---|---|
| Browse race index | none | — |
| View race detail | none | — |
| **Create** race | `organizer`+ | becomes the organizer |
| **Open registration** | `organizer`+ | own race only (`senior_organizer`+ override) |
| **Close registration** | `organizer`+ | own race only (`senior_organizer`+ override) |
| **Set room code** | `organizer`+ | own race only (`senior_organizer`+ override) |
| Register for race | `user`+ | self only |
| Unregister from race | `user`+ | self only OR organizer+ to remove others |
| **Submit results** | `organizer`+ | own race only (`senior_organizer`+ override) |
| Upload result screenshot | `organizer`+ | own race only (`senior_organizer`+ override) |
| **Cancel** race | `organizer`+ | own race only (`senior_organizer`+ override) |
| **Delete** race | `admin`+ | none — destructive smoke-test path |

### Draft matches

Draft matches don't have a single creator the way official races do
— they have host + opponent, and both opted into the match. The
ownership semantics are different:

| Action | Min role | Notes |
|---|---|---|
| Create draft match | `user`+ | becomes the host |
| Join via code | `user`+ | accepting party becomes opponent |
| Invite by username | `user`+ | host only (PR-I7) |
| Accept / decline invite | `user`+ | invitee only |
| Cancel invite | `user`+ | inviter only |
| Submit ready / ban / room code | `user`+ | match participants only (host + opponent) |
| Submit results | `user`+ | match participants OR `organizer`+ |
| Upload result screenshot | `user`+ | match participants OR `organizer`+ |
| Forfeit | `organizer`+ | dispute resolution path |
| **Cancel match** | `organizer`+ | any organizer (no ownership concept here) |
| **Edit completed results** | `admin`+ | recovery path; recomputes ELO |
| **Delete match** | `admin`+ | destructive smoke-test path |

### Champions Meeting

| Action | Min role | Ownership |
|---|---|---|
| View dashboard CM card | none | — |
| Manage CM (create/edit/delete) | `admin`+ | none |

### Admin-only surfaces

All `/admin/*` routes are gated to `admin`+. Includes: users list,
role changes, audit log, season management, draft matches list,
notification log, CM management, draft results edit.

### Anonymous

Public reads (no login required):

- Dashboard `/`
- Profile browse `/profiles/` and any public profile `/profiles/<u>`
- Official race index `/official/` and any race detail
- Draft index `/draft/` (sees logged-in matches only when authenticated)
- Skill catalogue `/skills/` (gated to `organizer`+ for the
  full catalogue — anonymous gets a stub)

## Implementation notes

- The decorator `min_role_required(Role.X)` lives in
  `services/permissions.py`. It's applied directly to the route
  function. Anonymous users get 401; under-rank users get 403.
- Service-level ownership check: `assert_can_act_on_race(race,
  by_user_id=...)` raises `PermissionDeniedError`. Routes that
  could have an ownership-denial outcome catch it and call
  `abort(403)`. Tests for the negative case live in
  `tests/test_official_routes.py` under "PR-J1 ownership-gated
  permissions".
- For draft matches the ownership concept is participants vs.
  organizer-moderator. The relevant helper is
  `_user_can_submit_results(match)` in `draft/routes.py`; it
  allows match participants OR `organizer`+.

## When you add a new state-changing route

Two-step protocol:

1. Pick the right `min_role_required(Role.X)` decorator. Floor it
   at `organizer` for organizer actions, `admin` for moderation,
   `user` for self-actions.
2. If the action is on an OfficialRace and is anything beyond
   "view," call `assert_can_act_on_race(race, by_user_id=...)` at
   the top of the service function. The route should catch
   `PermissionDeniedError` and `abort(403)`.

Then update **this document** so the matrix reflects reality. The
doc is the source of truth for "can role X do Y" — code drift from
this matrix is a bug.
