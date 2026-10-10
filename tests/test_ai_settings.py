import pytest
from sqlalchemy import select

from app import ai
from app.config import settings
from app.database import SessionLocal
from app.models import AppSetting, AuditLog
from app.services import ai_settings

KEY = "sk-test-SECRET-1234567890abcd"
GOOD = {"base_url": "https://api.example-llm.com/v1/", "model": "gpt-4o-mini", "api_key": KEY,
        "consent": "true"}


@pytest.fixture(autouse=True)
def clean_ai(_app_client):
    """Every test starts and ends with no saved AI settings and no leaked in-process values."""
    def reset():
        with SessionLocal() as db:
            ai_settings.clear(db)
    reset()
    yield
    reset()


def save(admin, **over):
    return admin.post("/ai-settings", {**GOOD, **over}, page="/ai-settings")


def test_admin_only(admin, make_user):
    for role in ("viewer", "engineer", "approver"):
        s = make_user(f"ai-{role}", role)
        assert s.get("/ai-settings").status_code == 403
        assert s.post("/ai-settings", GOOD, page="/").status_code == 403
        assert s.post("/ai-settings/test", page="/").status_code == 403
        assert s.post("/ai-settings/remove-key", page="/").status_code == 403
        assert s.post("/ai-settings/reset", page="/").status_code == 403
        assert 'href="/ai-settings"' not in s.get("/").text           # no nav link either
    assert 'href="/ai-settings"' in admin.get("/").text


def test_requires_login_and_csrf(admin):
    from fastapi.testclient import TestClient

    from app.main import app
    anon = TestClient(app, cookies={})
    assert anon.get("/ai-settings", follow_redirects=False).status_code == 303
    assert admin.client.post("/ai-settings", data=GOOD, follow_redirects=False).status_code == 403


def test_save_applies_encrypts_and_never_reveals_key(admin):
    assert save(admin).status_code == 303
    assert settings.llm_base_url == "https://api.example-llm.com/v1"      # trailing slash removed
    assert settings.llm_model == "gpt-4o-mini" and settings.llm_api_key == KEY
    assert settings.llm_enabled and settings.llm_send_fleet_data
    with SessionLocal() as db:
        raw = db.get(AppSetting, "llm_api_key_enc").value
        assert KEY not in raw and raw.startswith("gAAAA")                  # Fernet token, not plain text
        assert KEY not in " ".join(r.value for r in db.scalars(select(AppSetting)).all())
        logged = " ".join(f"{a.action} {a.detail}" for a in db.scalars(select(AuditLog)).all())
        assert "ai.settings.update" in logged and KEY not in logged and "key_changed=True" in logged
    page = admin.get("/ai-settings").text
    assert KEY not in page and "ends in abcd" in page and "AI is on" in page
    assert "https://api.example-llm.com/v1" in page


def test_blank_key_keeps_saved_key_and_remove_key_works(admin):
    save(admin)
    save(admin, api_key="", model="other-model")
    assert settings.llm_api_key == KEY and settings.llm_model == "other-model"
    admin.post("/ai-settings/remove-key", page="/ai-settings")
    assert settings.llm_api_key == ""
    with SessionLocal() as db:
        assert db.get(AppSetting, "llm_api_key_enc") is None


@pytest.mark.parametrize("field,value", [
    ("base_url", ""), ("base_url", "ftp://x.com/v1"), ("base_url", "javascript:alert(1)"),
    ("base_url", "http://api.example-llm.com/v1"),                    # cloud over plain http
    ("base_url", "https://user:pw@api.example-llm.com/v1"),           # credentials in the URL
    ("base_url", "https://api.example-llm.com/v1?key=abc"),
    ("base_url", "https://api.example-llm.com:99999/v1"),
    ("model", ""), ("model", "bad model;rm -rf"), ("model", "x" * 101),
    ("api_key", "has space"), ("api_key", "line\nbreak"),
])
def test_invalid_input_is_rejected_and_nothing_saved(admin, field, value):
    before = (settings.llm_base_url, settings.llm_model)
    save(admin, **{field: value})
    assert (settings.llm_base_url, settings.llm_model) == before
    with SessionLocal() as db:
        assert db.scalar(select(AppSetting)) is None
    assert 'class="flash error"' in admin.get("/ai-settings").text


@pytest.mark.parametrize("url", ["http://localhost:11434/v1", "http://10.1.2.3:8000/v1",
                                 "http://127.0.0.1:8080/v1", "https://api.example-llm.com/v1"])
def test_local_http_and_cloud_https_are_accepted(url):
    assert ai_settings.validate_base_url(url)[1] is None


def test_consent_and_off_switch(admin):
    save(admin, consent="")                       # an unchecked box is not sent at all
    assert settings.llm_enabled and not settings.llm_send_fleet_data
    with pytest.raises(ai.AdviceError):
        ai.explain_diagnostics("x")               # no consent: nothing is sent
    save(admin, off="true")
    assert not settings.llm_enabled
    assert "switched off" in admin.get("/ai-settings").text
    assert "needs an LLM" in ai.answer_question("q", "ctx")
    save(admin)
    assert settings.llm_enabled and settings.llm_send_fleet_data


def test_saved_values_override_environment_and_reset_restores_it(admin, monkeypatch):
    env = ai_settings._remember_env()
    monkeypatch.setitem(env, "llm_model", "env-model")
    monkeypatch.setitem(env, "llm_api_key", "env-key-0000000000")
    save(admin)
    assert settings.llm_model == "gpt-4o-mini" and settings.llm_api_key == KEY
    admin.post("/ai-settings/reset", page="/ai-settings")
    assert settings.llm_model == "env-model" and settings.llm_api_key == "env-key-0000000000"
    assert "environment" in admin.get("/ai-settings").text


def test_settings_survive_restart(admin):
    save(admin)
    settings.llm_base_url = settings.llm_model = settings.llm_api_key = ""        # as after a restart
    with SessionLocal() as db:
        ai_settings.apply(db)
    assert settings.llm_api_key == KEY and settings.llm_model == "gpt-4o-mini"


def test_wrong_encryption_key_is_not_fatal(admin):
    save(admin)
    with SessionLocal() as db:
        db.get(AppSetting, "llm_api_key_enc").value = "gAAAAnot-a-valid-token"
        db.commit()
        ai_settings.apply(db)                                      # must not raise
        assert settings.llm_api_key == "" and ai_settings.view(db)["key_source"] == ""

