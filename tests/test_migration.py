from pathlib import Path
import subprocess
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/bind9'


def test_snapshot_script_syntax():
    subprocess.run(['bash', '-n', str(ROLE / 'files/bind-backup.sh')], check=True)


def test_snapshot_before_configuration():
    data = yaml.safe_load((ROLE / 'tasks/main.yml').read_text())
    imports = [task.get('ansible.builtin.import_tasks') for task in data]
    assert imports.index('tasks.d/migration.yml') < imports.index('tasks.d/directories.yml')
    assert imports.index('tasks.d/migration.yml') < imports.index('tasks.d/transaction.yml')
    assert 'configuration.yml' in str(data)


def test_no_destructive_operations():
    script = (ROLE / 'files/bind-backup.sh').read_text()
    assert 'rm -rf' not in script
    assert 'systemctl stop' not in script
    assert 'tar --create --gzip --acls --xattrs --selinux' in script
    assert 'sha256sum --check' in script
    assert 'flock -x 9' in script
    assert 'baseline.sha256' in script
    main = (ROLE / 'tasks/main.yml').read_text()
    assert 'rescue:' in main and 'restore' in main


def test_no_legacy_modular_path():
    for file in (ROLE / 'templates').glob('*.j2'):
        assert '/etc/named/modular' not in file.read_text()


def test_existing_files_updated_without_removal():
    config = (ROLE / 'tasks/tasks.d/configuration.yml').read_text()
    assert 'ansible.builtin.template' in config
    assert 'state: absent' not in config
    assert 'force: false' not in config
