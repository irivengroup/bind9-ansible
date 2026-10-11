from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles" / "bind9"


def test_front_and_peer_views_separated():
    text = (ROLE / "templates/40-views.conf.j2").read_text()
    assert 'match-destinations { {{ bind_front_ip }}; };' in text
    assert 'match-destinations { {{ bind_back_ip }}; };' in text
    assert 'view "peer"' in text
    assert 'in-view "peer"' in (ROLE / "templates/20-forward-zones.conf.j2").read_text()
    assert 'recursion no;' in text


def test_backup_without_stop_or_delete():
    text = (ROLE / "files/bind-migrate.sh").read_text()
    assert 'sha256sum --check' in text
    assert 'systemctl stop named' not in text
    assert 'rm -rf' not in text


def test_inventory_vars_are_separated():
    inventory = (ROOT / "inventory/hosts.ini").read_text()
    assert 'ansible_host=' not in inventory
    assert len(list((ROOT / "inventory/host_vars").glob('*.yml'))) == 4
