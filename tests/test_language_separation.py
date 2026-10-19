from pathlib import Path
import re
import subprocess
ROOT = Path(__file__).resolve().parents[1] / 'roles/bind9/files'
def test_single_bash_script():
    script = ROOT / 'bind-configtool.sh'
    subprocess.run(['bash','-n',str(script)],check=True)
    assert not (ROOT / 'bind-migrate-backup.py').exists()
    assert not (ROOT / 'bind-migrate.sh').exists()
    assert 'python3' not in script.read_text()


def test_code_sources_have_safe_encoding_and_shell_sources_use_lf():
    """Keep shell shebangs portable; permit existing Windows CRLF in other text."""
    project = Path(__file__).resolve().parents[1]
    suffixes = {'.sh', '.py', '.yml', '.yaml', '.j2', '.ini', '.cfg', '.json'}
    for path in project.rglob('*'):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        data = path.read_bytes()
        data.decode('utf-8')
        assert not data.startswith(b'\xef\xbb\xbf'), path
        if path.suffix == '.sh':
            assert b'\r' not in data, path
        # CRLF is a valid existing source encoding for YAML/Python on Windows;
        # reject isolated carriage returns and all other hidden control bytes.
        text = data.replace(b'\r\n', b'\n')
        assert not any(value < 32 and value not in (9, 10) or value == 127 for value in text), path


def test_ci_keeps_shellcheck_complete_with_only_documented_trap_exceptions():
    """Prevent new blanket exclusions from hiding a real shell diagnostic."""
    project = Path(__file__).resolve().parents[1]
    workflow = (project / '.github/workflows/ci.yml').read_text()
    assert 'koalaman/shellcheck@sha256:61862eba1fcf09a484ebcc6feea46f1782532571a34ed51fedf90dd25f925a8d' in workflow
    assert '--norc --shell=bash roles/bind9/files/*.sh tests/*.sh' in workflow
    for path in project.rglob('*.sh'):
        directives = re.findall(r'(?m)^\s*#\s*shellcheck\s+disable=(.*)$', path.read_text())
        expected = ['SC2329', 'SC2329'] if path == ROOT / 'bind-front-routes.sh' else []
        assert directives == expected, path
