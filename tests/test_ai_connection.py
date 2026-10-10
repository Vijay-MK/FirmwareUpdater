import json

import httpx
import pytest

from app import ai
from app.config import settings
from app.database import SessionLocal
from app.services import ai_settings

KEY = "sk-live-TOPSECRET-9876543210wxyz"


@pytest.fixture()
def cloud(_app_client, monkeypatch):
    """A configured cloud endpoint whose HTTP calls are answered by a fake transport."""
    with SessionLocal() as db:
        ai_settings.save(db, "t", "https://api.example-llm.com/v1", "gpt-4o-mini", KEY, True, False)
    seen = []
    state = {"handler": None}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append({"url": url, "headers": headers, "json": json})
        return state["handler"](url)

    monkeypatch.setattr(httpx, "post", fake_post)
    yield seen, state
    with SessionLocal() as db:
        ai_settings.clear(db)


def reply(status=200, body=None):
    def make(url):
        req = httpx.Request("POST", url)
        return httpx.Response(status, json=body if body is not None else
                              {"choices": [{"message": {"content": "ok"}}]}, request=req)
    return make


def test_request_goes_to_the_saved_endpoint_with_bearer_key(cloud):
    seen, state = cloud
    state["handler"] = reply()
    assert ai.check_connection().startswith("Connection works")
    call = seen[0]
    assert call["url"] == "https://api.example-llm.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    assert call["json"]["model"] == "gpt-4o-mini"
    assert "plc" not in json.dumps(call["json"]).lower()              # no fleet or device data in the test


@pytest.mark.parametrize("status,hint", [(401, "rejected"), (403, "rejected"), (404, "base URL"),
                                         (429, "quota"), (500, "HTTP 500")])
def test_http_errors_get_a_clear_message(cloud, status, hint):
    _, state = cloud
    state["handler"] = reply(status, {"error": "x"})
    with pytest.raises(ai.AdviceError) as exc:
        ai.check_connection()
    assert hint in str(exc.value) and KEY not in str(exc.value)


def test_network_and_format_errors(cloud):
    _, state = cloud

    def down(url):
        raise httpx.ConnectError("dns failure for api.example-llm.com")
    state["handler"] = down
    with pytest.raises(ai.AdviceError, match="Could not reach"):
        ai.check_connection()
    state["handler"] = reply(200, {"unexpected": True})
    with pytest.raises(ai.AdviceError, match="OpenAI-compatible"):
        ai.check_connection()


def test_test_button_reports_result_and_audits_without_key(admin, cloud):
    _, state = cloud
    state["handler"] = reply()
    admin.post("/ai-settings/test", page="/ai-settings")
    assert "Connection works" in admin.get("/ai-settings").text
    state["handler"] = reply(401, {"error": "bad key"})
    admin.post("/ai-settings/test", page="/ai-settings")
    page = admin.get("/ai-settings").text
    assert "API key was rejected" in page and KEY not in page
    from sqlalchemy import select

    from app.models import AuditLog
    with SessionLocal() as db:
        rows = [f"{a.action} {a.detail}" for a in db.scalars(select(AuditLog)).all()
                if a.action == "ai.settings.test"]
    assert any("result=ok" in r for r in rows) and any("result=failed" in r for r in rows)
    assert all(KEY not in r for r in rows)


def test_diagnostics_explain_uses_cloud_settings_end_to_end(admin, cloud):
    from sqlalchemy import select

    from app.models import Device
    from tests import simulate_devices as sim
    from tests.sim_scenarios import SCENARIOS
    with SessionLocal() as db:
        sim.seed(db, SCENARIOS[:1], log=lambda *_: None)
        dev = db.scalar(select(Device.id).where(Device.ip_address == SCENARIOS[0].ip))
    seen, state = cloud
    state["handler"] = reply(200, {"choices": [{"message": {"content":
        '{"summary": "DB12 missing", "actions": ["Load DB12"], "escalate": false}'}}]})
    page = f"/devices/{dev}/diagnostics"
    assert 'disabled title="An administrator' not in admin.get(page).text      # button is enabled
    from tests.test_ai_diagnostics import explain
    explain(admin, dev)
    assert seen and seen[-1]["headers"]["Authorization"] == f"Bearer {KEY}"
    assert "DB12 missing" in admin.get(page).text
    with SessionLocal() as db:
        sim.clear(db, SCENARIOS[:1])
