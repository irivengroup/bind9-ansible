from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "roles/bind9/templates"

def test_descriptive_zone_module_names():
    assert (TEMPLATES / "10-authoritative-zones.conf.j2").is_file()
    assert (TEMPLATES / "20-forward-zones.conf.j2").is_file()
    assert not (TEMPLATES / "50-zones.conf.j2").exists()
    assert not (TEMPLATES / "60-front-zones.conf.j2").exists()
    views = (TEMPLATES / "40-views.conf.j2").read_text()
    assert 'include "/etc/named/zones/10-authoritative-zones.conf";' in views
    assert 'include "/etc/named/zones/20-forward-zones.conf";' in views
    main = (TEMPLATES / "named.conf.j2").read_text()
    assert "10-authoritative-zones.conf" not in main
    assert "20-forward-zones.conf" not in main
