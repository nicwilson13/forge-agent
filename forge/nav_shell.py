"""
Shared navigation shell for all Forge local app views.

Provides page_shell() which wraps page content with consistent
nav bar, font loading, keyboard shortcuts, and connection status.
This module imports only stdlib. No forge imports at module level.
"""


NAV_LINKS = [
    ("/", "Build"),
    ("/tasks", "Tasks"),
    ("/history", "History"),
    ("/integrations", "Integrations"),
    ("/linear", "Linear"),
    ("/setup", "Setup"),
]


def page_shell(
    title: str,
    active_route: str,
    body_content: str,
    extra_head: str = "",
    extra_scripts: str = "",
) -> str:
    """
    Wrap page content in the full Forge app shell.

    Returns complete HTML document string with:
    - <head> with Tailwind CDN, JetBrains Mono font, base dark styles
    - Fixed nav bar with active state highlighting
    - Task badge that polls /tasks/data every 30s
    - Keyboard shortcuts (g+b, g+t, g+h, g+i, g+l, g+s)
    - SSE connection indicator
    - body_content inserted after nav
    - extra_head inserted in <head> (for page-specific styles)
    - extra_scripts appended before </body>
    """
    nav_html = _build_nav(active_route)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Forge - {title}</title>
<script src="https://cdn.tailwindcss.com"></script>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  * {{ font-family: 'JetBrains Mono', monospace; }}
  body {{ background: #0f0f0f; color: #e5e5e5; margin: 0; }}
  ::-webkit-scrollbar {{ width: 6px; }}
  ::-webkit-scrollbar-track {{ background: #1a1a1a; }}
  ::-webkit-scrollbar-thumb {{ background: #333; border-radius: 3px; }}
</style>
{extra_head}
</head>
<body>

{nav_html}

{body_content}

<script>
// Keyboard shortcuts: g + key navigation
(function() {{
  let gPressed = false;
  let gTimer = null;
  document.addEventListener('keydown', function(e) {{
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
    if (e.key === 'g' && !e.ctrlKey && !e.metaKey && !e.altKey) {{
      gPressed = true;
      clearTimeout(gTimer);
      gTimer = setTimeout(function() {{ gPressed = false; }}, 1000);
      return;
    }}
    if (gPressed) {{
      gPressed = false;
      clearTimeout(gTimer);
      var routes = {{b:'/',t:'/tasks',h:'/history',i:'/integrations',l:'/linear',s:'/setup'}};
      if (routes[e.key]) {{ e.preventDefault(); window.location.href = routes[e.key]; }}
    }}
  }});
}})();

// Poll task badge every 30s
(function() {{
  function updateBadge() {{
    fetch('/tasks/data').then(function(r) {{ return r.json(); }}).then(function(d) {{
      var badge = document.getElementById('nav-task-badge');
      if (badge) {{
        var count = d.count || 0;
        badge.textContent = count;
        badge.style.display = count > 0 ? 'inline' : 'none';
      }}
    }}).catch(function() {{}});
  }}
  updateBadge();
  setInterval(updateBadge, 30000);
}})();

// SSE connection indicator
(function() {{
  var dot = document.getElementById('sse-status-dot');
  if (!dot) return;
  try {{
    var es = new EventSource('/events');
    es.onopen = function() {{ dot.style.background = '#00e5a0'; dot.title = 'Live'; }};
    es.onerror = function() {{ dot.style.background = '#666'; dot.title = 'Offline'; }};
  }} catch(e) {{
    dot.style.background = '#666'; dot.title = 'Offline';
  }}
}})();
</script>
{extra_scripts}
</body>
</html>"""


def _build_nav(active_route: str) -> str:
    """Build the nav bar HTML with active state."""
    links = []
    for href, label in NAV_LINKS:
        is_active = href == active_route
        if is_active:
            style = "font-size:13px;color:#fff;text-decoration:none;font-weight:600"
            hover = ""
        else:
            style = "font-size:13px;color:#999;text-decoration:none"
            hover = ' onmouseover="this.style.color=\'#fff\'" onmouseout="this.style.color=\'#999\'"'

        if label == "Tasks":
            badge = '<span id="nav-task-badge" style="display:none;background:#00e5a0;color:#0f0f0f;font-size:11px;padding:1px 6px;border-radius:8px;margin-left:4px">0</span>'
            links.append(f'<a href="{href}" style="{style}"{hover}>{label} {badge}</a>')
        else:
            links.append(f'<a href="{href}" style="{style}"{hover}>{label}</a>')

    links_html = "\n  ".join(links)

    return f"""<nav style="position:fixed;top:0;left:0;right:0;height:40px;background:#1a1a1a;border-bottom:1px solid #2a2a2a;display:flex;align-items:center;padding:0 16px;gap:24px;z-index:50">
  <span style="color:#00e5a0;font-weight:700;font-size:14px">forge</span>
  {links_html}
  <span id="sse-status-dot" style="margin-left:auto;width:8px;height:8px;border-radius:50%;background:#666" title="Offline"></span>
</nav>"""
