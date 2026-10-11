from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / 'roles' / 'bind9' / 'templates'


def test_named_conf_only_includes_modules():
    content = (TEMPLATES / 'named.conf.j2').read_text(encoding='utf-8')
    statements = [line.strip() for line in content.splitlines() if line.strip() and not line.lstrip().startswith('//')]
    assert statements == [
        f'include "/etc/named/{name}";'
        for name in ('10-tsig.conf', '20-acl.conf', '30-options.conf', '40-views.conf')
    ]


def test_zone_modules_are_included_in_views_only():
    content = (TEMPLATES / '40-views.conf.j2').read_text(encoding='utf-8')
    assert 'include "/etc/named/zones/10-authoritative-zones.conf";' in content
    assert 'include "/etc/named/zones/20-forward-zones.conf";' in content
    entry = (TEMPLATES / 'named.conf.j2').read_text(encoding='utf-8')
    assert '10-authoritative-zones.conf' not in entry
    assert '20-forward-zones.conf' not in entry
