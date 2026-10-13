from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "roles/bind9/templates"

def test_global_recursion_and_transfer_disabled():
    options = (TEMPLATES / "30-options.conf.j2").read_text()
    for directive in ("recursion no;", "allow-recursion { none; };", "allow-query-cache { none; };", "allow-transfer { none; };"):
        assert directive in options

def test_external_view_explicit_recursion_override():
    views = (TEMPLATES / "40-views.conf.j2").read_text()
    external = views.split('view "external" IN {', 1)[1].split('view "deny" IN {', 1)[0]
    assert "recursion yes;" in external
    assert "allow-recursion { frontend_clients; };" in external
    assert "allow-query-cache { frontend_clients; };" in external
    assert 'include "/etc/named/zones/20-forward-zones.conf";' in external
