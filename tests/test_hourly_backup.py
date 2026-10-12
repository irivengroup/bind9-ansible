from pathlib import Path
import subprocess
import yaml
ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/bind9'
def test_backup_helper_parses_and_enforces_policy():
    source = (ROLE / 'files/bind-backup.sh').read_text()
    subprocess.run(['bash','-n',str(ROLE / 'files/bind-backup.sh')],check=True)
    for token in ('date -u', 'BACKUP_REUSED', 'BACKUP_CREATED', 'sha256sum', 'named-checkconf', 'restorecon', 'flock', 'RESTORED'):
        assert token in source
def test_backup_before_mutations():
    tasks = yaml.safe_load((ROLE / 'tasks/main.yml').read_text())
    backup = next(i for i,t in enumerate(tasks) if 'tasks.d/migration.yml' in str(t))
    directories = next(i for i,t in enumerate(tasks) if 'tasks.d/directories.yml' in str(t))
    transaction = next(i for i,t in enumerate(tasks) if 'tasks.d/transaction.yml' in str(t))
    assert backup < directories < transaction
    assert 'bind-backup.sh' in str(yaml.safe_load((ROLE/'tasks/tasks.d/migration.yml').read_text()))
