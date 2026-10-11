from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_zone_config_modules():
    templates = ROOT / 'roles/bind9/templates'
    assert (templates / '10-authoritative-zones.conf.j2').is_file()
    assert (templates / '20-forward-zones.conf.j2').is_file()
    views = (templates / '40-views.conf.j2').read_text()
    assert '/etc/named/zones/10-authoritative-zones.conf' in views
    assert '/etc/named/zones/20-forward-zones.conf' in views
    entry = (templates / 'named.conf.j2').read_text()
    assert '/etc/named/zones/' not in entry

def test_non_destructive_creation():
    tasks = (ROOT / 'roles/bind9/tasks/configuration.yml').read_text()
    dirs = (ROOT / 'roles/bind9/tasks/directories.yml').read_text()
    assert '/etc/named/zones/{{ item.dest }}' in tasks
    assert '/etc/named/zones' in dirs
    assert 'state: absent' not in tasks
