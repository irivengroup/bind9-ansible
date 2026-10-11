from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]

def test_ordered_inventory():
    import configparser
    config = configparser.ConfigParser(allow_no_value=True, delimiters=('=',))
    config.read(ROOT / 'inventory/hosts.ini')
    hosts = list(config['dns_deployment_order'])
    assert hosts == ['zibo-x1-dns01', 'zibo-x3-dns01', 'zibo-x1-dns02', 'zibo-x3-dns02']

def test_one_play_one_role():
    data = yaml.safe_load((ROOT / 'playbooks/site.yml').read_text())
    assert len(data) == 1
    assert data[0]['roles'] == ['bind9']
    assert data[0]['serial'] == 1
    assert data[0]['order'] == 'inventory'

def test_primary_gate_before_migration():
    text = (ROOT / 'roles/bind9/tasks/main.yml').read_text()
    assert text.index('primary_readiness.yml') < text.index('migration.yml')
