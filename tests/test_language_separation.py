from pathlib import Path
import subprocess
ROOT = Path(__file__).resolve().parents[1] / 'roles/bind9/files'
def test_single_bash_script():
    script = ROOT / 'bind-backup.sh'
    subprocess.run(['bash','-n',str(script)],check=True)
    assert not (ROOT / 'bind-migrate-backup.py').exists()
    assert not (ROOT / 'bind-migrate.sh').exists()
    assert 'python3' not in script.read_text()
