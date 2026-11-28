"""Exercise the guide through the same HTTP entry point a remote MCP client uses."""
from fastapi.testclient import TestClient

from apps.api.application import app
from apps.api.routes.app_guide import GUIDE_FILE


def test_chatgpt_can_read_only_the_static_product_guide():
    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(app, base_url="https://onflows-api-staging.onrender.com") as client:
        def rpc(method, params):
            response = client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
            })
            assert response.status_code == 200
            return response.json()

        handshake = rpc("initialize", {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "onflows-test", "version": "1"},
        })["result"]
        assert handshake["serverInfo"]["name"] == "onFlows"
        headers["MCP-Protocol-Version"] = handshake["protocolVersion"]
        ready = client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })
        assert ready.status_code == 202
        tools = rpc("tools/list", {})["result"]["tools"]
        assert [tool["name"] for tool in tools] == ["get_app_guide"]
        assert tools[0]["annotations"]["readOnlyHint"]
        assert tools[0]["inputSchema"]["properties"] == {}
        result = rpc("tools/call", {"name": "get_app_guide", "arguments": {}})["result"]
        assert result["content"][0]["text"] == GUIDE_FILE.read_text(encoding="utf-8")
        unavailable = rpc("tools/call", {"name": "change_calendar_event", "arguments": {}})
        assert "error" in unavailable or unavailable["result"]["isError"]
