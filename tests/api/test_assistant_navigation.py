from pathlib import Path

import pytest
from fastapi import HTTPException

from apps.api import assistant_navigation


@pytest.mark.parametrize("key", [
    "account", "profile", "password", "organizations", "invitations",
    "assignments", "athletes", "link", "connect", "refresh", "logout",
])
def test_handoffs_never_report_a_completed_mutation_or_grant_account_roles(key):
    result = assistant_navigation.read(
        {"can_view_plan": True, "can_edit_plan": True, "is_owner": False}, key)
    assert result["status"] == "HANDOFF_REQUIRED"
    assert result["performed"] is False
    assert result["account_permissions"] == "CHECKED_BY_AUTHENTICATED_WEB_FORM"
    assert len(result["links"]) == 1
    assert result["links"][0]["key"] == key
    assert "receipt" not in result
    assert "can_manage" not in result


@pytest.mark.parametrize("key", [
    "https://example.com", "//example.com", "/api/account/password",
    "javascript:alert(1)", "account?actor=someone", {"key": "account"},
])
def test_navigation_cannot_be_used_as_arbitrary_url_tool(key):
    with pytest.raises(HTTPException) as exc:
        assistant_navigation.read({"can_view_recovery": True}, key)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("access", [{}, {"can_edit_plan": True}, {"can_view_plan": "true"}])
def test_revoked_or_malformed_grant_cannot_access_navigation(access):
    with pytest.raises(HTTPException) as exc:
        assistant_navigation.read(access, "account")
    assert exc.value.status_code == 403


def test_recovery_only_viewer_can_open_their_account_without_plan_access():
    result = assistant_navigation.read({"can_view_recovery": True}, "athletes")
    assert result["links"][0]["href"] == "/account#athletes-title"
    assert "отделен разговор" in result["links"][0]["instruction"]


def test_plan_only_editor_uses_the_sync_control_on_the_page_they_can_open():
    result = assistant_navigation.read({"can_view_plan": True, "can_edit_plan": True}, "refresh")
    assert result["links"][0]["href"] == "/management"


def test_plan_only_viewer_is_not_sent_to_an_unavailable_refresh_control():
    result = assistant_navigation.read({"can_view_plan": True})
    assert "refresh" not in [item["key"] for item in result["links"]]
    with pytest.raises(HTTPException) as exc:
        assistant_navigation.read({"can_view_plan": True}, "refresh")
    assert exc.value.status_code == 403


def test_navigation_destinations_are_real_existing_pages_and_anchors():
    root = Path(__file__).resolve().parents[2] / "apps" / "web"
    account_sources = "\n".join((root / path).read_text() for path in (
        "app/account/page.tsx", "components/account-workspace.tsx",
        "components/password-settings.tsx",
    ))
    for item in assistant_navigation._DESTINATIONS.values():
        href, _, anchor = item["href"].partition("#")
        if href == "/api/integrations/intervals/connect":
            assert (root / "app/api/integrations/intervals/connect/route.ts").is_file()
        else:
            assert (root / "app" / href.strip("/") / "page.tsx").is_file()
        if anchor:
            assert f'id="{anchor}"' in account_sources


def test_result_mutation_does_not_change_future_handoffs():
    first = assistant_navigation.read({"can_view_plan": True}, "profile")
    first["links"][0]["href"] = "https://example.com"
    assert assistant_navigation.read({"can_view_plan": True}, "profile")["links"][0]["href"] == "/account"
