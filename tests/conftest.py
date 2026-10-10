import os
import re
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="fwmgr_test_")
os.environ["FWMGR_DATA_DIR"] = _TMP
os.environ["FWMGR_ADMIN_PASSWORD"] = "admin-password-123"
os.environ["FWMGR_ENABLE_SCHEDULER"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PASSWORD = "admin-password-123"


def token_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "no csrf token on page"
    return match.group(1)


class Session:
    """A logged-in browser-like session that handles CSRF tokens automatically."""

    def __init__(self, client: TestClient):
        self.client = client

    def login(self, username: str, password: str):
        page = self.client.get("/login")
        return self.client.post("/login", data={"username": username, "password": password,
                                                "csrf_token": token_from(page.text)},
                                follow_redirects=False)

    def post(self, url: str, data: dict | None = None, files=None, page: str | None = None):
        html = self.client.get(page or "/").text
        payload = dict(data or {})
        payload["csrf_token"] = token_from(html)
        return self.client.post(url, data=payload, files=files, follow_redirects=False)

    def get(self, url: str):
        return self.client.get(url)


@pytest.fixture(scope="session")
def _app_client():
    with TestClient(app) as client:
        yield client
    shutil.rmtree(_TMP, ignore_errors=True)


def _make_session(app_client, username, password):
    # Separate cookie jar per session.
    client = TestClient(app, cookies={})
    s = Session(client)
    resp = s.login(username, password)
    assert resp.status_code == 303, resp.text
    return s


@pytest.fixture()
def admin(_app_client):
    return _make_session(_app_client, "admin", PASSWORD)


@pytest.fixture()
def make_user(_app_client, admin):
    def factory(username: str, role: str) -> Session:
        admin.post("/users/new", {"username": username, "password": "password-for-" + username,
                                  "role": role}, page="/users")
        return _make_session(_app_client, username, "password-for-" + username)
    return factory


@pytest.fixture(autouse=True)
def _reset_sim():
    from app.drivers import simulator
    simulator.reset()
    yield
