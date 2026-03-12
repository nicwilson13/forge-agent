"""Tests for forge.nav_shell."""

from forge.nav_shell import page_shell, NAV_LINKS


def test_page_shell_contains_nav():
    """page_shell output contains nav element with all link hrefs."""
    html = page_shell("Test", "/", "<div>content</div>")
    assert "<nav" in html
    for href, label in NAV_LINKS:
        assert f'href="{href}"' in html
        assert label in html


def test_page_shell_active_route_highlighted():
    """Active route has white text, others have muted."""
    html = page_shell("Tasks", "/tasks", "<div/>")
    # The /tasks link should have color:#fff (active)
    assert "color:#fff" in html
    # Other links should have color:#999
    assert "color:#999" in html


def test_page_shell_contains_keyboard_shortcuts():
    """Output includes keyboard shortcut JS."""
    html = page_shell("Test", "/", "<div/>")
    assert "gPressed" in html
    assert "routes" in html


def test_page_shell_contains_badge_poll():
    """Output includes task badge polling JS."""
    html = page_shell("Test", "/", "<div/>")
    assert "/tasks/data" in html
    assert "updateBadge" in html
    assert "30000" in html


def test_page_shell_sets_page_title():
    """<title> contains the provided title string."""
    html = page_shell("My Page", "/", "<div/>")
    assert "<title>Forge - My Page</title>" in html


def test_all_views_use_page_shell():
    """All 6 HTML constants contain nav links (from page_shell)."""
    from forge.dashboard import INDEX_HTML
    from forge.tasks_view import TASKS_HTML
    from forge.history_view import HISTORY_HTML
    from forge.integrations_view import INTEGRATIONS_HTML
    from forge.linear_view import LINEAR_HTML
    from forge.setup_wizard import SETUP_HTML

    for name, html in [
        ("INDEX", INDEX_HTML),
        ("TASKS", TASKS_HTML),
        ("HISTORY", HISTORY_HTML),
        ("INTEGRATIONS", INTEGRATIONS_HTML),
        ("LINEAR", LINEAR_HTML),
        ("SETUP", SETUP_HTML),
    ]:
        assert "/linear" in html, f"{name} missing /linear"
        assert "/history" in html, f"{name} missing /history"
        assert "/integrations" in html, f"{name} missing /integrations"
        assert "nav-task-badge" in html, f"{name} missing task badge"
