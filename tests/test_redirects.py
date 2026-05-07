"""PR-J8 — unit tests for safe_redirect_target.

These cover the helper directly so each of its rules has named
coverage; the integration tests on auth / admin / draft routes
verify the helper is plumbed into the right call sites.
"""

from __future__ import annotations

import pytest

from uma_ladder.services.redirects import safe_redirect_target


@pytest.mark.parametrize(
    "candidate",
    [
        "/dashboard",
        "/draft/123",
        "/profiles/me",
        "/draft/?page=2",
        "/draft/123#tab-results",
    ],
)
def test_clean_relative_paths_pass(candidate: str) -> None:
    assert safe_redirect_target(candidate, default="/D") == candidate


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        "",
        "   ",
        "dashboard",  # missing leading slash
        "https://evil.com/x",
        "http://evil.com",
        "//evil.com/x",  # protocol-relative
        "/\\evil.com",  # backslash bypass
        "javascript:alert(1)",
        "data:text/html,<script>",
    ],
)
def test_external_or_malformed_falls_back_to_default(
    candidate: str | None,
) -> None:
    assert safe_redirect_target(candidate, default="/D") == "/D"


def test_same_origin_absolute_url_is_stripped_to_path() -> None:
    """Used by request.referrer flows — full URL with matching host
    becomes a relative path so the redirect Location is hostless."""
    assert (
        safe_redirect_target(
            "https://umaladder.moe/draft/123?x=1#frag",
            default="/D",
            current_host="umaladder.moe",
        )
        == "/draft/123?x=1#frag"
    )


def test_same_origin_with_port_match_passes() -> None:
    """request.host includes port; referrer parsed.netloc does too.
    They must compare equal for localhost dev to keep working."""
    assert (
        safe_redirect_target(
            "http://localhost:5000/draft/9",
            default="/D",
            current_host="localhost:5000",
        )
        == "/draft/9"
    )


def test_absolute_url_with_mismatched_host_rejected() -> None:
    assert (
        safe_redirect_target(
            "https://evil.com/draft/123",
            default="/D",
            current_host="umaladder.moe",
        )
        == "/D"
    )


def test_absolute_url_without_current_host_rejected() -> None:
    """Callers that don't supply current_host (login `next`, admin
    form `next`) should refuse all absolute URLs — even ones that
    happen to match the deploy domain."""
    assert (
        safe_redirect_target(
            "https://umaladder.moe/x", default="/D"
        )
        == "/D"
    )


def test_whitespace_padding_is_trimmed() -> None:
    assert (
        safe_redirect_target("  /draft/1  ", default="/D")
        == "/draft/1"
    )
