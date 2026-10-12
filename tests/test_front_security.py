import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "roles/bind9/templates"


def test_front_view_is_nonrecursive_and_has_no_forward_include():
    content = (TEMPLATES / "40-views.conf.j2").read_text()
    external = content.split('view "external" IN {', 1)[1].split('view "deny" IN {', 1)[0]
    assert "recursion no;" in external
    assert "allow-recursion { none; };" in external
    assert "allow-query-cache { none; };" in external
    assert 'include "/etc/named/zones/20-forward-zones.conf"' not in external


def test_client_address_filter():
    path = ROOT / "roles/bind9/filter_plugins/client_acl.py"
    spec = importlib.util.spec_from_file_location("client_acl", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.valid_client_address("198.51.100.0/24")
    assert module.valid_client_address("2001:db8::/32")
    for valid in ["192.0.2.100", "2001:db8::1", "0.0.0.0", "::1"]:
        assert module.valid_client_address(valid)
    for invalid in ["198.51.100.4/24", "198.51.100.0/33", "203.0.113.0/24;", "bad", "2001:db8::1/32", "192.0.2.0/abc"]:
        assert not module.valid_client_address(invalid)
