from pathlib import Path
import configparser
import yaml
ROOT = Path(__file__).resolve().parents[1]
def test_inventory_sites_and_order():
    config = configparser.ConfigParser(allow_no_value=True)
    config.read(ROOT / 'inventory/hosts.ini')
    assert len(config['zibo_x1']) == len(config['zibo_x3']) == 2
    assert list(config['dns_deployment_order']) == ['zibo-x1-dns01', 'zibo-x3-dns01', 'zibo-x1-dns02', 'zibo-x3-dns02']
def test_site_variables():
    for site in ('zibo_x1', 'zibo_x3'):
        values = yaml.safe_load((ROOT / 'inventory/group_vars' / (site + '.yml')).read_text())
        assert values['bind_primary_back_ip'] != values['bind_secondary_back_ip']
def test_default_inventory():
    assert 'inventory/hosts.ini' in (ROOT / 'ansible.cfg').read_text()
