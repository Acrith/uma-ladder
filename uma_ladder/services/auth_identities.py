"""Resolve OAuth ``ProviderProfile`` payloads to local ``User``s.

Sits on top of ``services.oauth`` and the ``auth_identities``
table. The route layer handles HTTP shape; this module handles
the find/create/link/unlink semantics so the same logic is shared
by login, settings UI, and (later) admin tooling.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

from sqlalchemy import select

from ..extensions import db
from ..models import AuthIdentity, Role, User, UserProfile
from .auth import find_user_by_username
from .oauth import ProviderProfile
from .profiles import get_or_create_profile


class AuthIdentityError(Exception):
    """Base for resolution failures."""


class IdentityAlreadyLinkedError(AuthIdentityError):
    """The (provider, external_id) is already linked to a different
    user. Surfaces in the "link my Discord" flow when someone tries
    to attach an identity that another account owns."""


class UsernameDerivationError(AuthIdentityError):
    """Couldn't pick a unique local username from the OAuth
    profile. Practically never happens (we have ~16M suffix slots
    per base name) but we surface it cleanly rather than silently
    creating a garbage username."""


# ─── Identity lookup ─────────────────────────────────────────────


def find_identity(provider: str, external_id: str) -> AuthIdentity | None:
    return db.session.scalars(
        select(AuthIdentity).where(
            AuthIdentity.provider == provider,
            AuthIdentity.external_id == external_id,
        )
    ).first()


def list_identities_for_user(user: User) -> list[AuthIdentity]:
    return list(
        db.session.scalars(
            select(AuthIdentity)
            .where(AuthIdentity.user_id == user.id)
            .order_by(AuthIdentity.linked_at.asc())
        )
    )


# ─── Linking ─────────────────────────────────────────────────────


def link_identity(user: User, profile: ProviderProfile) -> AuthIdentity:
    """Attach an OAuth identity to an existing user.

    Raises ``IdentityAlreadyLinkedError`` if the (provider,
    external_id) tuple is already owned by a different user — never
    silently steal an identity from another account.

    For Discord specifically, also mirror the snowflake onto
    ``UserProfile.discord_user_id`` so existing notification
    `<@id>` pings keep working without the user having to set the
    field manually. The OAuth flow is more authoritative than the
    manual entry — we just always overwrite.
    """
    existing = find_identity(profile.provider, profile.external_id)
    if existing is not None:
        if existing.user_id == user.id:
            _refresh_identity_fields(existing, profile)
            db.session.commit()
            return existing
        raise IdentityAlreadyLinkedError(
            f"{profile.provider} identity {profile.external_id!r} "
            f"is already linked to another account"
        )

    identity = AuthIdentity(
        user_id=user.id,
        provider=profile.provider,
        external_id=profile.external_id,
        external_username=profile.external_username,
        email=profile.email,
        avatar_url=profile.avatar_url,
    )
    db.session.add(identity)

    if profile.provider == "discord":
        prof = get_or_create_profile(user)
        prof.discord_user_id = profile.external_id
        if not prof.discord_handle and profile.external_username:
            prof.discord_handle = profile.external_username

    db.session.commit()
    return identity


def unlink_identity(user: User, provider: str) -> bool:
    """Remove the user's identity for ``provider``. Returns True
    if a row was removed, False if there was nothing to unlink.

    Defensive: if removing this identity would leave an OAuth-only
    user with no way to log in (no other identities + unguessable
    password), the caller should refuse — but the policy lives at
    the route/UI layer. This function is mechanical."""
    identity = db.session.scalars(
        select(AuthIdentity).where(
            AuthIdentity.user_id == user.id,
            AuthIdentity.provider == provider,
        )
    ).first()
    if identity is None:
        return False
    db.session.delete(identity)
    db.session.commit()
    return True


def touch_login(identity: AuthIdentity) -> None:
    """Stamp ``last_login_at`` on a successful OAuth login. Keeps
    the column useful for "last seen" displays + admin audits
    without us needing a separate AuditLog row per login."""
    identity.last_login_at = datetime.now(UTC)
    db.session.commit()


def _refresh_identity_fields(
    identity: AuthIdentity, profile: ProviderProfile
) -> None:
    """Re-link of an existing identity should refresh display data
    (username changes, avatar swaps) without creating a new row."""
    identity.external_username = profile.external_username
    identity.email = profile.email
    identity.avatar_url = profile.avatar_url


# ─── Account creation from OAuth ─────────────────────────────────


def create_user_for_oauth(profile: ProviderProfile) -> User:
    """Create a brand-new local user from an OAuth profile and
    link the identity in one transaction.

    Username is derived from the provider's display name (Discord
    username / Google email handle) with sanitization + suffix-on-
    collision. The user gets an unguessable random password —
    they can never log in via password, but they can recover via
    the admin-reset flow if they ever lose their Discord access.
    """
    username = _derive_unique_username(profile)

    user = User(username=username, role=Role.USER)
    # Random unguessable password. OAuth-only users can't password-
    # log-in; admin-issued reset stays available as a fallback if
    # they ever lose Discord access.
    user.set_password(secrets.token_urlsafe(64))
    db.session.add(user)
    db.session.flush()  # need user.id for the identity row

    identity = AuthIdentity(
        user_id=user.id,
        provider=profile.provider,
        external_id=profile.external_id,
        external_username=profile.external_username,
        email=profile.email,
        avatar_url=profile.avatar_url,
    )
    db.session.add(identity)

    if profile.provider == "discord":
        # New user → no UserProfile exists yet. Add one inline so
        # the verified Discord snowflake powers `<@id>` mention
        # pings out of the box without a manual profile-edit step.
        db.session.add(
            UserProfile(
                user_id=user.id,
                discord_user_id=profile.external_id,
                discord_handle=profile.external_username,
            )
        )

    db.session.commit()
    return user


_USERNAME_ALLOWED = re.compile(r"[^a-z0-9_.]")


def _derive_unique_username(profile: ProviderProfile) -> str:
    """Pick a 3-64-char lowercase username from the OAuth profile,
    suffix-on-collision against existing users.

    Discord usernames (post-2023 unique handles) are already
    lowercase ASCII + dot + underscore, so the sanitization is
    mostly a no-op for that provider. Falls back to a
    provider-prefixed slice of the external_id if the display name
    sanitizes to nothing (rare — would require a username made
    entirely of non-ASCII).
    """
    base = (profile.external_username or "").strip().lower()
    base = _USERNAME_ALLOWED.sub("", base)
    if len(base) < 3:
        # Discord snowflakes are 17-19 digits; prefix with the
        # provider's first letter to keep collision chances low
        # against any human-typed username.
        prefix = (profile.provider[:1] or "x")
        base = f"{prefix}{profile.external_id}"
    base = base[:60]

    candidate = base
    for _ in range(8):
        if find_user_by_username(candidate) is None:
            return candidate
        suffix = secrets.token_hex(2)  # 4 hex chars
        candidate = f"{base[:55]}_{suffix}"
    raise UsernameDerivationError(
        f"could not derive unique username from {base!r} "
        "after 8 suffix attempts"
    )
