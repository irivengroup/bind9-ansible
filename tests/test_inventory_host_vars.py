from pathlib import Path
import configparser
import yaml

ROOT = Path(__file__).resolve().parents[1] / 'inventory'

def test_host_vars():
    config = configparser.ConfigParser(allow_no_value=True)
    config.read(ROOT / 'hosts.ini')
    assert len(list((ROOT / 'host_vars').glob('*.yml'))) == 4
    for site in ('zibo_x1', 'zibo_x3'):
        for host in config[site]:
            assert '=' not in host
            data = yaml.safe_load((ROOT / 'host_vars' / f'{host}.yml').read_text())
            assert set(data) == {'ansible_host', 'bind_front_ip', 'bind_back_ip', 'bind_role'}
    assert list(config['dns_deployment_order']) == ['zibo-x1-dns01','zibo-x3-dns01','zibo-x1-dns02','zibo-x3-dns02']
