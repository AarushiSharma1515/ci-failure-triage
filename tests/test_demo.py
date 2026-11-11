from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

DEMO = Path(__file__).resolve().parent.parent / "app" / "static" / "demo.html"


def test_demo_page_is_served():
    r = TestClient(app).get("/demo")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Phase 1: rule-based baseline" in r.text


def test_demo_never_uses_inner_html():
    # Logs must be rendered with textContent so pasted text cannot inject HTML.
    assert "innerHTML" not in DEMO.read_text(encoding="utf-8")


def test_demo_loads_nothing_from_external_sites():
    html = DEMO.read_text(encoding="utf-8")
    assert "<script src" not in html
    assert "<link" not in html
    assert "http://" not in html and "https://" not in html
