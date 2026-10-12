from pathlib import Path
import subprocess
import yaml
ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/bind9'

def test_migration_script_valid():
    subprocess.run(['bash','-n',str(ROLE/'files/bind-migrate.sh')], check=True)

def test_non_destructive_migration():
    tasks = yaml.safe_load((ROLE/'tasks/main.yml').read_text())
    assert any(t.get('ansible.builtin.import_tasks') == 'tasks.d/migration.yml' for t in tasks)
    assert any('rescue' in t for t in tasks)


def test_no_secret_logged_in_config_task():
    tasks = yaml.safe_load((ROLE/'tasks/tasks.d/configuration.yml').read_text())
    assert any(t.get('no_log') is True for t in tasks if 'TSIG' in t['name'])

def test_network_policy_external():
    assert not (ROLE/'tasks/tasks.d/firewalld.yml').exists()
    assert (ROOT/'docs/NETWORK_POLICY.md').exists()

def test_numbered_includes():
    text = (ROLE/'templates/named.conf.j2').read_text()
    for n in ('10-tsig','20-acl','30-options','40-views'):
        assert n in text
