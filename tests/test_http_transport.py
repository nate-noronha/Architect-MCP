"""Slice 7 tests: Remote HTTP/SSE transport, /health endpoint & deployment readiness."""

from pathlib import Path

from starlette.testclient import TestClient

from architect_mcp.server import create_mcp_server


def test_health_endpoint_on_streamable_http_app(tmp_path: Path):
    """Verify `/health` returns 200 OK with active tenant and bucket counts on HTTP app."""
    app = create_mcp_server(
        tenant_slug="hmc-makerspace",
        database_url=f"sqlite:///{tmp_path / 'http_health.db'}",
    )
    client = TestClient(app.streamable_http_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "architect-mcp"
    assert data["university_slug"] == "hmc-makerspace"
    assert data["buckets"]["safety"] >= 1
    assert data["buckets"]["machines"] >= 1
    assert data["buckets"]["inventory"] >= 1


def test_health_endpoint_on_sse_app(tmp_path: Path):
    """Verify `/health` returns 200 OK on the SSE ASGI app as well."""
    app = create_mcp_server(
        tenant_slug="mit-makerworks-demo",
        database_url=f"sqlite:///{tmp_path / 'sse_health.db'}",
    )
    client = TestClient(app.sse_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["university_slug"] == "mit-makerworks-demo"


def test_streamable_http_mcp_initialize_handshake(tmp_path: Path):
    """Verify Streamable HTTP `/mcp` endpoint handles JSON-RPC initialize requests."""
    app = create_mcp_server(
        tenant_slug="hmc-makerspace",
        database_url=f"sqlite:///{tmp_path / 'http_mcp.db'}",
    )
    with TestClient(
        app.streamable_http_app(), base_url="http://127.0.0.1:8765"
    ) as client:
        resp = client.post(
            "/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest-http-client", "version": "1.0.0"},
                },
            },
        )
        assert resp.status_code == 200
        assert "architect-mcp" in resp.text
