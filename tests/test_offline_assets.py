"""The page's scripts (Tailwind, htmx, Chart.js) are served by the app itself, so the demo looks and
works the same on bad venue wifi."""
import re

from fastapi.testclient import TestClient

from app.main import app


def test_page_loads_no_scripts_from_the_internet():
    html = TestClient(app).get("/").text
    scripts = re.findall(r'<script[^>]*src="([^"]+)"', html)
    assert scripts and all(s.startswith("/static/") for s in scripts), scripts


def test_bundled_scripts_are_served():
    c = TestClient(app)
    for path in re.findall(r'<script[^>]*src="(/static/[^"]+)"', c.get("/").text):
        r = c.get(path)
        assert r.status_code == 200 and len(r.content) > 1000, path
