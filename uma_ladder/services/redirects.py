"""Safe redirect helpers (PR-J8).

`request.args.get("next")`, `request.form.get("next")`, and
`request.referrer` all carry user-controlled URLs into our
`redirect()` calls. Letting them through unfiltered is the classic
open-redirect vector — phishing pages link
`https://umaladder.moe/auth/login?next=https://evil.com/...` so
the post-login bounce lands on attacker turf, where a fake
"session expired" prompt harvests the freshly-typed password.

`safe_redirect_target()` is the single gate every call site goes
through. The rule is: only accept clean same-origin destinations.
Relative paths pass straight through; absolute URLs pass only when
their netloc matches the current request's host (and we strip them
down to a path so the response Location header is hostless).
"""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def safe_redirect_target(
    candidate: str | None,
    *,
    default: str,
    current_host: str | None = None,
) -> str:
    """Return `candidate` only when it points to a same-origin
    destination, otherwise `default`.

    Accepts:
    - relative path starting with ``/`` (e.g. ``/dashboard``)
    - absolute URL whose netloc matches ``current_host`` — used by
      callers that pass ``request.referrer`` (which is a full URL).
      The return value is rewritten to a path so the response
      Location header doesn't echo the host.

    Rejects:
    - empty / whitespace
    - external URLs (scheme + non-matching host, e.g.
      ``https://evil.com``)
    - protocol-relative URLs (``//evil.com``)
    - backslash variants browsers may treat as slashes
      (``/\\evil.com``)
    """
    if not candidate:
        return default
    target = candidate.strip()
    if not target:
        return default

    parsed = urlparse(target)

    # Absolute URL: accept only when the host matches the current
    # request. Strip scheme+host so the redirect Location is
    # path-only (no double-encoding worries, no host echo).
    if parsed.scheme or parsed.netloc:
        if (
            current_host
            and parsed.netloc == current_host
            and parsed.scheme in ("", "http", "https")
        ):
            return urlunparse(
                ("", "", parsed.path or "/", parsed.params, parsed.query, parsed.fragment)
            )
        return default

    # Relative path. Must start with `/` — anything else is
    # ambiguous. Reject protocol-relative and backslash bypasses.
    if not target.startswith("/"):
        return default
    if target.startswith(("//", "/\\")):
        return default
    return target
