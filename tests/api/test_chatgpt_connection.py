from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api import assistant_commands, assistant_context, chatgpt_tools, dependencies
from apps.api.application import app
from apps.api.routes import chatgpt_oauth as oauth
from tests.api.test_assistant_day_projection import record
from tests.api.test_assistant_planning_commands import Repository


def test_connected_plan_and_calendar(monkeypatch):
    monkeypatch.setenv("ONFLOWS_WEB_BASE_URL", oauth.WEB)
    actor, alias = str(uuid4()), "athlete-test"
    repo = Repository()
    repo.athlete_settings = lambda _: SimpleNamespace(timezone="UTC")
    monkeypatch.setattr(dependencies, "repository", lambda: repo)
    monkeypatch.setattr(assistant_context, "PlanStore", lambda _: SimpleNamespace(current=lambda _: record()))
    rights = {"can_view_plan": True, "can_edit_plan": True, "can_view_recovery": False, "is_owner": False}
    grant = {"actor": actor, "athlete_alias": alias, "scopes": [oauth.READ]}
    stored = {}
    def access(repository, athlete, user):
        assert athlete == alias and user == actor
        return rights.copy()
    monkeypatch.setattr(assistant_commands, "access", access)
    monkeypatch.setattr(oauth, "read_grant", lambda _, token: grant if token == "a"*43 else None)
    monkeypatch.setattr(assistant_commands, "operation_status",
                        lambda repo, alias, actor, op: stored.get(op, {"status": "NOT_FOUND"}))
    def save(repository, athlete, user, operation, prepared, request_hash, **kwargs):
        assert repository is repo and athlete == alias and user == actor
        repo.calendar = prepared["payload"]
        stored[operation] = {"status": "SAVED", "revision": 5}
        return stored[operation]
    monkeypatch.setattr(assistant_commands, "apply_prepared", save)
    with TestClient(app, base_url=oauth.ISSUER) as client:
        def invoke(name, arguments, credential="a"*43):
            result = client.post("/mcp", headers={"Accept": "application/json, text/event-stream",
                "Authorization": "Bearer " + credential}, json={"jsonrpc":"2.0", "id":1, "method":"tools/call",
                "params":{"name":name,"arguments":arguments}})
            return result.json()["result"]
        assert "mcp/www_authenticate" in invoke("list_races", {}, "")["_meta"]
        day = invoke("get_training_day", {"day": "2026-11-13"})["structuredContent"]
        assert len(day["sessions"]) == 2 and day["sessions"][0]["title"] == "Session 1"
        assert "input_snapshot" not in day
        calendar = invoke("list_races", {})["structuredContent"]
        args = dict(event_id=calendar["events"][0]["event_id"], calendar_revision=calendar["revision"],
                    new_start_date="2026-12-30", athlete_alias=alias)
        assert invoke("move_race_date", args)["isError"] and not stored
        grant["scopes"].append(oauth.WRITE)
        assert invoke("move_race_date", {**args,"athlete_alias":"other-athlete"})["isError"] and not stored
        assert invoke("move_race_date", {**args,"calendar_revision":1})["isError"] and not stored
        assert invoke("move_race_date", args)["structuredContent"]["status"] == "SAVED"
        assert invoke("list_races", {})["structuredContent"]["events"][0]["start_date"] == "2026-12-30"
        assert invoke("move_race_date", args)["structuredContent"]["already_applied"] and len(stored) == 1
        rights["can_edit_plan"] = False
        assert invoke("move_race_date", args)["isError"]
        rights["can_view_plan"] = False
        assert invoke("list_races", {})["isError"]

        rights["can_view_plan"] = rights["can_edit_plan"] = True
        readonly = "another-athlete"
        hidden = "removed-athlete"
        def roster_access(repository, athlete, user):
            if athlete == hidden:
                from fastapi import HTTPException
                raise HTTPException(403)
            assert athlete in {alias, readonly}
            return rights.copy()
        monkeypatch.setattr(assistant_commands, "access", roster_access)
        grant.update(athlete_alias=None, onflows_chatgpt_athletes=[
            {"athlete_alias": key, "allow_write": key == alias,
             "onflows_user_athletes": {"onflows_profiles": {"display_name": "Иван"}}}
            for key in (alias, readonly, hidden)])
        roster = invoke("get_connected_athlete", {})["structuredContent"]["athletes"]
        assert [p["athlete_alias"] for p in roster] == [alias, readonly]
        assert [p["can_move_race"] for p in roster] == [True, False]
        assert invoke("list_races", {})["isError"]
        assert invoke("list_races", {"athlete_alias": "unselected"})["isError"]
        assert invoke("list_races", {"athlete_alias": hidden})["isError"]
        assert invoke("list_races", {"athlete_alias": readonly})["structuredContent"]["athlete_alias"] == readonly
        assert invoke("move_race_date", {**args, "athlete_alias": readonly})["isError"]
        assert invoke("move_race_date", args)["structuredContent"]["athlete_alias"] == alias


def test_oauth_browser_to_pkce_exchange(monkeypatch):
    from urllib.parse import urlsplit, parse_qs
    import secrets
    from unittest.mock import Mock
    monkeypatch.setenv("ONFLOWS_WEB_BASE_URL", oauth.WEB)
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "test-internal")
    repo = Mock()
    repo._json.side_effect = lambda x: x
    repo._request.return_value = [oauth.READ]
    monkeypatch.setattr(dependencies,"repository",lambda:repo)
    monkeypatch.setattr(assistant_commands,"access",lambda *args: {"can_view_plan":True,"can_edit_plan":True})
    verifier = secrets.token_urlsafe(40)
    params = dict(client_id=oauth.CLIENT, redirect_uri=oauth.CALLBACK, resource=oauth.RESOURCE,
                  response_type="code", code_challenge_method="S256", code_challenge=oauth.pkce(verifier),
                  state="opaque-state", scope="plan.read calendar.write")
    body = dict(actor=str(uuid4()), athlete_alias="test-athlete", approve=True, params=params)
    with TestClient(app,base_url=oauth.ISSUER) as client:
        assert client.get("/.well-known/oauth-protected-resource/mcp").json()["resource"] == oauth.RESOURCE
        endpoint="/api/v2/integrations/chatgpt/consent"
        headers={"Authorization":"Bearer test-internal"}
        assert client.post(endpoint,json=body).status_code == 401
        bad = {**body,"params":{**params,"redirect_uri":"https://invalid.example"}}
        assert client.post(endpoint,headers=headers,json=bad).status_code == 400
        write_alone = {**body, "params": {**params, "scope": oauth.WRITE}}
        assert client.post(endpoint, headers=headers, json=write_alone).status_code == 400
        result=client.post(endpoint,headers=headers,json=body)
        callback=parse_qs(urlsplit(result.json()["location"]).query)
        assert callback["state"] == ["opaque-state"] and callback["iss"] == [oauth.ISSUER]
        saved=repo._request.call_args.kwargs["json"]
        assert saved["code_hash"]==oauth.digest(callback["code"][0]) and saved["scopes"]==[oauth.READ]
        protected=client.post("/oauth/chatgpt/token",data={"client_id":oauth.CLIENT,"resource":oauth.RESOURCE,
            "grant_type":"authorization_code","redirect_uri":oauth.CALLBACK,"code_verifier":verifier,
            "code":callback["code"][0]})
        token=protected.json()
        assert token["scope"]=="plan.read" and protected.headers["Cache-Control"]=="no-store"
        exchange=repo._request.call_args.kwargs["json"]
        assert exchange["p_challenge"]==saved["challenge"] and exchange["p_access"]==oauth.digest(token["access_token"])
