import http.client
import json
import threading

import pytest

from swing.server import LocalServer, Worker
from swing.store import Store


@pytest.fixture
def server(tmp_path):
    instance = LocalServer(Store(tmp_path / "http.sqlite3"), 0)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance
    instance.shutdown()
    instance.server_close()
    thread.join(timeout=3)


def request(server, method, path, payload=None, **headers):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    if payload is not None:
        body = json.dumps(payload)
        standard = {
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{server.server_port}",
            "X-Swing-Local": "1",
        }
    else:
        body, standard = None, {}
    conn.request(method, path, body, {**standard, **headers})
    response = conn.getresponse()
    data = response.read()
    result = (response.status, json.loads(data), dict(response.headers))
    conn.close()
    return result


def test_api_full_flow_security_and_persistence(server):
    status, state, headers = request(server, "GET", "/api/state")
    assert status == 200
    assert state["worker"]["running"] is False
    assert state["worker"]["broker_enabled"] is False
    assert headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert request(server, "POST", "/api/fixture", {})[0] == 200
    status, result, _ = request(server, "POST", "/api/step", {"expected_cursor": None})
    assert status == 200 and result["cursor"] == "2026-01-02"
    assert (
        request(server, "POST", "/api/step", {"expected_cursor": None})[1]["status"] == "unchanged"
    )
    assert request(server, "POST", "/api/step", {})[0] == 422
    assert request(server, "POST", "/api/settings", state["settings"])[0] == 422
    export = request(server, "GET", "/api/export")[1]
    assert len(export["bars"]) == 300
    assert export["metadata"] and export["batches"]
    assert export["state"]["cursor"] == "2026-01-02"
    assert request(server, "GET", "/.env")[0] == 404
    assert request(server, "GET", "/../store.py")[0] == 404
    assert request(server, "GET", "/api/state", Host="evil.test")[0] == 403
    assert request(server, "GET", "/api/state", **{"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert request(server, "OPTIONS", "/api/step")[0] == 403


@pytest.mark.parametrize(
    "override",
    [
        {"Host": "evil.test"},
        {"Origin": "https://evil.test"},
        {"Origin": "null"},
        {"X-Swing-Local": ""},
        {"Content-Type": "text/plain"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": "http://localhost:8000"},
    ],
)
def test_mutation_rejects_forged_browser_requests(server, override):
    status, _, _ = request(server, "POST", "/api/fixture", {}, **override)
    assert status == 403
    assert request(server, "GET", "/api/state")[1]["data"]["count"] == 0


def test_missing_origin_rejected(server):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
    conn.request(
        "POST", "/api/fixture", "{}", {"Content-Type": "application/json", "X-Swing-Local": "1"}
    )
    response = conn.getresponse()
    assert response.status == 403
    response.read()
    conn.close()


def test_malformed_and_invalid_input_is_distinct_error(server):
    assert request(server, "POST", "/api/import", {"bars": []})[0] == 422
    assert request(server, "POST", "/api/step", {"expected_cursor": None})[0] == 422
    assert request(server, "GET", "/api/state")[1]["journal"][0]["kind"] == "error"


def test_worker_singleton_control_and_restart_defaults_stopped(server):
    request(server, "POST", "/api/fixture", {})
    assert request(server, "POST", "/api/worker/start", {})[1]["running"]
    first = server.worker.thread
    request(server, "POST", "/api/worker/start", {})
    assert server.worker.thread is first
    assert request(server, "POST", "/api/worker/stop", {})[1]["running"] is False
    assert Worker(Store(server.store.path)).status()["running"] is False


def test_worker_failure_visible_and_stops(server):
    server.worker.start()
    server.worker.thread.join(timeout=3)
    assert server.worker.status()["running"] is False
    assert "Importer" in server.worker.status()["error"]
    assert request(server, "GET", "/api/state")[1]["journal"][0]["kind"] == "worker_error"
