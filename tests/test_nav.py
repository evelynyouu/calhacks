"""Sidebar states (open / collapsed with tooltips / hidden) and Switch customer in the header. Rendered HTML only."""
from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.main import app
from tests.fund_helpers import make_fund

client = TestClient(app)
LABELS = ["Portfolio", "Money", "Markets", "News", "Chat", "Report", "Backtest", "Account"]


def aside(page: str) -> str:
    return page[page.index('<aside id="nav"'):page.index("</aside>")]


def header(page: str) -> str:
    start = page.index("<header")
    return page[start:page.index("</header>", start)]


def test_every_link_has_an_aria_label_and_a_tooltip():
    make_fund("sam")
    nav = aside(client.get("/p/sam").text)
    links = re.findall(r'<a href="/p/sam[^"]*" class="side-link[^>]*>.*?</a>', nav, re.S)
    assert len(links) == len(LABELS)
    for html, label in zip(links, LABELS):
        assert f'aria-label="{label}"' in html
        assert f'<span class="side-tip" aria-hidden="true">{label}</span>' in html
        assert "title=" not in html          # no native tooltip doubling up with ours


def test_tooltips_show_on_hover_and_focus_only_when_collapsed():
    css = client.get("/static/nav.css").text
    assert ".side-tip { display:none; }" in css
    assert "html.nav-collapsed .side-link:hover .side-tip, html.nav-collapsed .side-link:focus-visible .side-tip { display:block; }" in css


def test_hide_and_show_buttons():
    make_fund("sam")
    page = client.get("/p/sam").text
    nav = aside(page)
    assert 'data-nav-hide aria-controls="nav"' in nav and "Hide sidebar" in nav
    assert "data-nav-collapse" in nav
    head = header(page)      # the Show button sits in the header, outside the sidebar it brings back
    assert '<button type="button" class="nav-show" data-nav-show aria-controls="nav" aria-label="Show sidebar"' in head
    css = client.get("/static/nav.css").text
    assert "html.nav-hidden .side { display:none; }" in css and "html.nav-hidden .nav-show { display:inline-flex; }" in css


def test_state_is_restored_before_paint_and_saved():
    page = client.get("/p/alex").text
    head = page[:page.index("</head>")]
    assert "localStorage.getItem('ff-nav')" in head and "ffNav === 'hidden'" in head and "try {" in head
    js = client.get("/static/app.js").text
    assert "function setNavState(state)" in js and "try { localStorage.setItem(NAV_KEY, state); }" in js
    assert "[data-nav-hide]" in js and "[data-nav-show]" in js


def test_switch_customer_is_in_the_header_not_the_sidebar():
    make_fund("sam")
    for path in ("/p/sam", "/p/sam/money", "/p/sam/account", "/p/sam/report"):
        page = client.get(path).text
        assert "Switch customer" not in aside(page), path
        assert 'href="/"' not in aside(page), path
        head = header(page)
        assert '<a href="/" class="switch-cust" data-switch-customer aria-label="Switch customer (now Sam)"' in head, path
        assert page.count("data-switch-customer") == 1, path


def test_switch_customer_during_the_intake_and_link_steps():
    client.post("/p/maria/link", data={"checking": "checking", "investing": "investing"})
    assert "data-switch-customer" in header(client.get("/p/maria").text)


def test_no_switch_customer_without_a_customer():
    assert "data-switch-customer" not in client.get("/").text
