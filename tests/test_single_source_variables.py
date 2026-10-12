from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/bind9'


def test_no_shadow_variables():
    defaults = yaml.safe_load((ROLE / 'defaults/main.yml').read_text())
    forbidden = {'bind_role','bind_tsig_name','bind_firewalld_front_clients',
                 'bind_firewalld_primary_back_ip','bind_firewalld_secondary_back_ip',
                 'bind_primary_back_ip','bind_secondary_back_ip',
                 'bind_primary_front_ip','bind_secondary_front_ip'}
    assert not (set(defaults) & forbidden)
    for path in (ROOT / 'inventory').rglob('*.yml'):
        data = yaml.safe_load(path.read_text()) or {}
        assert not (set(data) & forbidden), path
    for path in (ROLE / 'tasks').rglob('*.yml'):
        assert not any(f'{name}:' in path.read_text() for name in forbidden), path


def test_roles_and_peers_are_inventory_driven():
    for path in (ROOT / 'inventory/host_vars').glob('*.yml'):
        data = yaml.safe_load(path.read_text())
        assert set(data) == {'bind_front_ip','bind_back_ip','bind_front_interface','bind_back_interface'}
    peers = (ROLE / 'tasks/tasks.d/site_peers.yml').read_text()
    assert "groups['dns_primary']" in peers
    assert "groups['dns_secondary']" in peers
    assert 'bind_primary_host:' in peers and 'bind_secondary_host:' in peers


def test_tsig_key_name_is_canonical():
    tsig = (ROLE / 'templates/10-tsig.conf.j2').read_text()
    assert 'bind_tsig_key_name' in tsig
    assert 'bind_tsig_name' not in tsig
