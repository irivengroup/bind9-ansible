from pathlib import Path
import configparser
import yaml
ROOT = Path(__file__).resolve().parents[1]
def test_inventory_sites_and_order():
    config = configparser.ConfigParser(allow_no_value=True)
    config.read(ROOT / 'inventory/hosts.ini')
    assert len(config['site1']) == len(config['site3']) == 2
    assert list(config['dns_deployment_order']) == ['site1-dns01', 'site3-dns01', 'site1-dns02', 'site3-dns02']
def test_site_variables():
    for site in ('site1', 'site3'):
        values = yaml.safe_load((ROOT / 'inventory/group_vars' / (site + '.yml')).read_text())
        assert 'bind_primary_back_ip' not in values and 'bind_secondary_back_ip' not in values
def test_default_inventory():
    assert 'inventory/hosts.ini' in (ROOT / 'ansible.cfg').read_text()
