from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / 'roles' / 'bind9' / 'templates'
TASKS = ROOT / 'roles' / 'bind9' / 'tasks'


def test_no_nested_named_d_in_deployment():
    for directory in (TEMPLATES, TASKS):
        for path in directory.rglob('*'):
            if path.is_file():
                assert '/etc/named/named.d' not in path.read_text(), str(path)


def test_named_entrypoint_is_includes_only():
    lines = [s.strip() for s in (TEMPLATES / 'named.conf.j2').read_text().splitlines() if s.strip()]
    assert len(lines) == 4
    assert all(s.startswith('include "/etc/named/') and s.endswith('";') for s in lines)


def test_zone_modules_in_view():
    views = (TEMPLATES / '40-views.conf.j2').read_text()
    assert 'include "/etc/named/zones/10-authoritative-zones.conf";' in views
    assert 'include "/etc/named/zones/20-forward-zones.conf";' in views
