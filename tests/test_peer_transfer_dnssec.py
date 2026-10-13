from pathlib import Path

T = Path(__file__).resolve().parents[1] / "roles/bind9/templates"

def test_dnssec_auto_and_global_transfer_deny():
    options = (T / "30-options.conf.j2").read_text()
    assert "dnssec-validation auto;" in options
    assert "allow-transfer { none; };" in options

def test_peer_only_zone_transfers():
    views = (T / "40-views.conf.j2").read_text()
    peer = views.split('view "peer" IN {', 1)[1].split('view "external" IN {', 1)[0]
    external = views.split('view "external" IN {', 1)[1].split('view "deny" IN {', 1)[0]
    zones = (T / "10-authoritative-zones.conf.j2").read_text()
    assert 'include "/etc/named/zones/10-authoritative-zones.conf";' in peer
    assert 'include "/etc/named/zones/10-authoritative-zones.conf";' not in external
    assert 'allow-transfer { none; };' in external
    assert 'allow-transfer { key "{{ bind_tsig_key_name }}"; };' in zones
    assert 'allow-transfer { none; };' in zones
    assert 'in-view "peer";' in (T / "15-external-authoritative-zones.conf.j2").read_text()
