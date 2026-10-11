from pathlib import Path
import subprocess
import yaml
ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/bind9'

def test_migration_script_valid():
    subprocess.run(['bash','-n',str(ROLE/'files/bind-migrate.sh')], check=True)

def test_non_destructive_migration():
    tasks = yaml.safe_load((ROLE/'tasks/main.yml').read_text())
    assert any(t.get('ansible.builtin.import_tasks') == 'migration.yml' for t in tasks)
    assert any('rescue' in t for t in tasks)


def test_no_secret_logged_in_config_task():
    tasks = yaml.safe_load((ROLE/'tasks/configuration.yml').read_text())
    assert any(t.get('no_log') is True for t in tasks if 'TSIG' in t['name'])

def test_back_interface_restrictions():
    t = (ROLE/'tasks/firewalld.yml').read_text()
    assert 'bind_firewalld_secondary_back_ip' in t
    assert 'bind_firewalld_primary_back_ip' in t
    assert 'Deny all other incoming DNS traffic on back' in t

def test_numbered_includes():
    text = (ROLE/'templates/named.conf.j2').read_text()
    for n in ('10-tsig','20-acl','30-options','40-views'):
        assert n in text
