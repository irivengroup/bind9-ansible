from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "roles" / "bind9"

def test_include_order():
    text = (ROOT / "templates/named.conf.j2").read_text()
    includes = [f"/etc/named/{n}" for n in ("10-tsig.conf", "20-acl.conf", "30-options.conf", "40-views.conf")]
    assert all(x in text for x in includes)
    assert [text.index(x) for x in includes] == sorted(text.index(x) for x in includes)
    assert "10-authoritative-zones.conf" not in text

def test_zones_in_view():
    assert "/etc/named/zones/10-authoritative-zones.conf" in (ROOT / "templates/40-views.conf.j2").read_text()

def test_templates_and_tasks():
    task = (ROOT / "tasks/tasks.d/configuration.yml").read_text()
    for name in ("10-tsig.conf", "20-acl.conf", "30-options.conf", "40-views.conf", "10-authoritative-zones.conf"):
        assert (ROOT / "templates" / (name + ".j2")).exists()
        assert name in task
