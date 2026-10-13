from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "roles/bind9/templates"

def test_source_addresses_only_in_peer_view():
    options = (ROOT / "30-options.conf.j2").read_text()
    views = (ROOT / "40-views.conf.j2").read_text()
    for directive in ("query-source", "transfer-source", "notify-source"):
        assert directive not in options
    peer = views.split('view "peer" IN {', 1)[1].split('view "external" IN {', 1)[0]
    assert "transfer-source {{ bind_back_ip }};" in peer
    assert "notify-source {{ bind_back_ip }};" in peer
    assert "allow-transfer { none; };" in peer
    assert "query-source" not in views

def test_secondary_notifications_require_tsig():
    zones = (ROOT / "10-authoritative-zones.conf.j2").read_text()
    assert 'allow-notify { key "{{ bind_tsig_key_name }}"; };' in zones
    assert 'allow-transfer { key "{{ bind_tsig_key_name }}"; };' in zones
    assert "transfer-source" not in zones
