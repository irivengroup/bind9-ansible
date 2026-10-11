from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_templates_present():
    for name in ('named.conf', '20-acl.conf', '30-options.conf', '40-views.conf', '10-authoritative-zones.conf', '20-forward-zones.conf', '10-tsig.conf', 'empty-root.zone', 'authoritative.zone', 'limits.conf'):
        assert (ROOT / 'roles/bind9/templates' / f'{name}.j2').is_file()

def test_no_unrestricted_transfers():
    s = (ROOT / 'roles/bind9/templates/10-authoritative-zones.conf.j2').read_text()
    assert 'allow-transfer { key' in s
    assert 'allow-transfer { none; }' in s
    assert 'transfer-source {{ bind_back_ip }};' in s

def test_forwarding_is_conditional():
    options = (ROOT / 'roles/bind9/templates/30-options.conf.j2').read_text()
    zones = (ROOT / 'roles/bind9/templates/20-forward-zones.conf.j2').read_text()
    assert 'forwarders' not in options
    assert 'type forward;' in zones
    assert 'zone "."' in zones

def test_tsig_secret_not_hardcoded():
    s = (ROOT / 'roles/bind9/templates/10-tsig.conf.j2').read_text()
    assert '{{ bind_tsig_secret }}' in s
