from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T = ROOT / 'roles/bind9/templates'

def test_forward_file_exclusively_forward_zones():
    text = (T / '20-forward-zones.conf.j2').read_text()
    assert 'in-view' not in text
    assert 'bind_authoritative_zones' not in text
    assert 'type forward;' in text

def test_external_authoritative_references_separate():
    views = (T / '40-views.conf.j2').read_text()
    assert 'include "/etc/named/zones/15-external-authoritative-zones.conf";' in views
    assert views.index('15-external-authoritative-zones.conf') < views.index('20-forward-zones.conf')
    shared = (T / '15-external-authoritative-zones.conf.j2').read_text()
    assert 'in-view "peer";' in shared
    assert 'type forward;' not in shared
    task = (ROOT / 'roles/bind9/tasks/tasks.d/configuration.yml').read_text()
    assert '15-external-authoritative-zones.conf.j2' in task
    transaction = (ROOT / 'roles/bind9/files/named-transaction.py').read_text()
    assert "'/etc/named/zones/15-external-authoritative-zones.conf'" in transaction
