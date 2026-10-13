from pathlib import Path
import yaml
ROOT = Path(__file__).resolve().parents[1]

def test_ips_only_in_hostvars():
    for site in ('site1', 'site3'):
        vars_ = yaml.safe_load((ROOT / 'inventory/group_vars' / (site+'.yml')).read_text())
        assert not any(k in vars_ for k in ('bind_primary_back_ip','bind_secondary_back_ip','bind_primary_front_ip','bind_secondary_front_ip','bind_firewalld_primary_back_ip','bind_firewalld_secondary_back_ip'))
        for suffix in ('dns01','dns02'):
            host = yaml.safe_load((ROOT/'inventory/host_vars'/f'{site}-{suffix}.yml').read_text())
            assert all(k in host for k in ('bind_front_ip','bind_back_ip'))

def test_peer_resolution_task():
    content = (ROOT/'roles/bind9/tasks/tasks.d/site_peers.yml').read_text()
    assert 'bind_primary_host:' in content
    assert 'bind_secondary_host:' in content
    assert 'bind_primary_back_ip:' not in content
