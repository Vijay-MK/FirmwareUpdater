from tests.test_workflow import add_release, add_sim_device


def test_every_page_renders_for_admin(admin):
    dev = add_sim_device(admin, name="pages", ip="10.7.0.1", version="1.0.0")
    add_release(admin, version="3.3.0")
    pages = ["/", "/devices", "/devices?q=pages&vendor=WAGO", "/devices/new", f"/devices/{dev}",
             f"/devices/{dev}/edit", "/firmware", "/firmware?vendor=WAGO", "/updates",
             "/notifications", "/assistant", "/account", "/users", "/audit"]
    for url in pages:
        r = admin.get(url)
        assert r.status_code == 200, f"{url} -> {r.status_code}"
    assert "3.3.0" in admin.get(f"/devices/{dev}").text


def test_assistant_explains_when_llm_disabled(admin):
    r = admin.post("/assistant", {"question": "Which devices are outdated?"}, page="/assistant")
    assert r.status_code == 200 and "LLM" in r.text


def test_missing_objects_return_404(admin):
    assert admin.get("/devices/99999").status_code == 404
    assert admin.get("/updates/99999").status_code == 404


def test_user_cannot_demote_self(admin):
    from sqlalchemy import select
    from app.database import SessionLocal
    from app.models import User
    with SessionLocal() as db:
        uid = db.scalar(select(User).where(User.username == "admin")).id
    admin.post(f"/users/{uid}/update", {"role": "viewer", "active": "true"}, page="/users")
    with SessionLocal() as db:
        assert db.get(User, uid).role == "admin"


def test_inactive_user_is_locked_out(admin, make_user):
    from sqlalchemy import select
    from app.database import SessionLocal
    from app.models import User
    victim = make_user("lockme", "viewer")
    with SessionLocal() as db:
        uid = db.scalar(select(User).where(User.username == "lockme")).id
    admin.post(f"/users/{uid}/update", {"role": "viewer"}, page="/users")  # active omitted = False
    r = victim.get("/devices", )
    assert r.status_code == 200 and "Sign in" in r.text  # redirected to login
