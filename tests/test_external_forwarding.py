from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T = ROOT / "roles/bind9/templates"

def test_external_forwarding_only():
    views = (T / "40-views.conf.j2").read_text()
    assert 'view "backend"' not in views
    assert 'view "external"' in views
    assert 'include "/etc/named/zones/20-forward-zones.conf";' in views
    assert 'recursion yes;' in views
    assert 'allow-recursion { frontend_clients; };' in views
    assert 'allow-query-cache { frontend_clients; };' in views
    assert 'allow-query { frontend_clients; };' in views

def test_internal_client_acl_is_conditional():
    views = (T / "40-views.conf.j2").read_text()
    assert 'bind_backend_clients | default([], true) | length > 0' in views
    assert 'view "internal" IN {' in views
    assert 'match-destinations { {{ bind_back_ip }}; };' in views
    assert 'allow-recursion { backend_clients; };' in views
    assert 'allow-transfer { none; };' in views
    assert views.index('view "peer"') < views.index('view "internal"')
