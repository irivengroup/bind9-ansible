from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]

def test_environment_inventories_and_isolation():
    for env in ('prod', 'dev', 'preprod'):
        base = ROOT / 'inventory' / env
        assert (base / 'hosts.ini').exists()
        assert yaml.safe_load((base / 'group_vars/all.yml').read_text())['bind_environment'] == env
        site = yaml.safe_load((base / 'group_vars/site1.yml').read_text())
        assert site['bind_site_name'] == 'site1'
        assert env in site['bind_tsig_secret_file']
        assert site['bind_cloud_clients']
        assert site['bind_authoritative_zones'][0]['domain'].endswith(f'{env}.example.test')

def test_peer_validation_checks_environment():
    content = (ROOT / 'roles/bind9/tasks/tasks.d/site_peers.yml').read_text()
    assert 'bind_environment is defined' in content
    assert 'bind_environment == bind_environment' in content
    assert 'bind_site_name == bind_site_name' in content
