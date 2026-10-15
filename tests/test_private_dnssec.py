"""Regress private forwarding without weakening strict zones or root validation."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest
from jinja2 import Environment, StrictUndefined
import yaml

ROLE = Path(__file__).resolve().parents[1] / 'roles/bind9'
SCRIPT = ROLE / 'files/bind-private-dnssec.py'
spec = importlib.util.spec_from_file_location('private_dnssec', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
PrivateDnssec = module.PrivateDnssec
ROOT_ONLY = pytest.mark.skipif(os.geteuid() != 0, reason='Private root-owned state requires Linux root')


def policy(domain='corp.example.test'):
    return '// Bind9-Ansible DNSSEC-PRIVATE ' + domain + '\n\nzone "' + domain + '" IN {\n type forward;\n};\n'


class TestPrivateDnssec:
    """Exercise real file safety and journal ownership with only RNDC doubled."""

    @pytest.mark.parametrize('domains', ['corp.test', ['.'], ['x.test;'], ['x.test\ny.test'],
                                         ['x.test', 'x.test'], [None], ['x.' + 'a' * 64],
                                         ['x.test'] * 1025])
    def test_invalid_suffix_lists_are_rejected(self, domains):
        with pytest.raises(ValueError):
            PrivateDnssec.domains(domains)

    def test_suffixes_are_canonical_and_sorted(self):
        assert PrivateDnssec.domains(['z.test', 'a.test']) == ['a.test', 'z.test']

    @ROOT_ONLY
    def test_renewal_removal_and_failed_operation_keep_only_owned_state(self, tmp_path):
        config, state = tmp_path / 'named.conf', tmp_path / 'state/owned.json'
        config.write_text(policy())
        operations = []
        def runner(argv, **kwargs):
            operations.append(argv)
            assert kwargs['timeout'] == 15
            return SimpleNamespace(returncode=0)
        helper = PrivateDnssec(config, state, runner=runner)
        assert helper.preview() == {'check': True, 'renew': ['corp.example.test'], 'remove': []}
        assert operations == [] and not state.parent.exists()
        assert helper.reconcile() == {'renewed': ['corp.example.test'], 'removed': []}
        assert operations[-1][-4:] == ['-force', '-lifetime', '1w', 'corp.example.test']
        assert '-s' in operations[-1] and '127.0.0.1' in operations[-1]
        assert stat.S_IMODE(state.stat().st_mode) == 0o600
        helper.reconcile()
        assert len(operations) == 2
        config.write_text(policy('new.example.test'))
        assert helper.reconcile() == {'renewed': ['new.example.test'], 'removed': ['corp.example.test']}
        assert operations[-2][-2:] == ['-remove', 'corp.example.test']
        config.write_text('options { dnssec-validation auto; };\n')
        assert helper.reconcile() == {'renewed': [], 'removed': ['new.example.test']}
        assert helper.owned() == []
        config.write_text(policy())
        helper.runner = lambda *args, **kwargs: SimpleNamespace(returncode=1)
        with pytest.raises(RuntimeError, match='RNDC NTA'):
            helper.reconcile()
        assert helper.owned() == ['corp.example.test']  # Recoverable after interruption.
        helper.runner = runner
        config.write_text('options { dnssec-validation auto; };\n')
        assert helper.reconcile()['removed'] == ['corp.example.test']

    @ROOT_ONLY
    @pytest.mark.parametrize('content', [policy().replace('type forward', 'type master'),
                                        policy().replace('zone "corp', 'zone "other'),
                                        '// Bind9-Ansible DNSSEC-PRIVATE corp.test\n',
                                        policy() + policy()])
    def test_malformed_or_duplicate_marker_fails_before_rndc(self, tmp_path, content):
        config = tmp_path / 'named.conf'
        config.write_text(content)
        with pytest.raises(ValueError):
            PrivateDnssec(config, tmp_path / 'state/owned.json').reconcile()
        assert not (tmp_path / 'state').exists()

    @ROOT_ONLY
    def test_case_insensitive_zone_matches_canonical_marker(self, tmp_path):
        config = tmp_path / 'named.conf'
        config.write_text(policy().replace('zone "corp.example.test"', 'zone "CORP.EXAMPLE.TEST"'))
        assert PrivateDnssec(config).desired() == ['corp.example.test']

    @ROOT_ONLY
    def test_unsafe_or_oversized_files_and_symlinks_are_rejected(self, tmp_path, monkeypatch):
        path = tmp_path / 'file'
        path.write_text('x')
        path.chmod(0o666)
        with pytest.raises(ValueError, match='permissions'):
            PrivateDnssec.read(path)
        path.chmod(0o600)
        actual_fstat = module.os.fstat
        monkeypatch.setattr(module.os, 'fstat', lambda fd: SimpleNamespace(st_mode=0o100600, st_uid=1000))
        with pytest.raises(ValueError, match='permissions'):
            PrivateDnssec.read(path)
        monkeypatch.setattr(module.os, 'fstat', actual_fstat)
        monkeypatch.setattr(PrivateDnssec, 'LIMIT', 2)
        path.write_text('xxx')
        with pytest.raises(ValueError, match='size limit'):
            PrivateDnssec.read(path)
        link = tmp_path / 'link'
        link.symlink_to(path)
        with pytest.raises(OSError):
            PrivateDnssec.read(link)

    @ROOT_ONLY
    @pytest.mark.parametrize('value', [[], {}, {'version': True, 'domains': []},
                                      {'version': 2, 'domains': []},
                                      {'version': 1, 'domains': ['.']}])
    def test_invalid_existing_journal_fails_closed(self, tmp_path, value):
        path = tmp_path / 'owned.json'
        path.write_text(json.dumps(value))
        with pytest.raises(ValueError):
            PrivateDnssec(state=path).owned()

    @ROOT_ONLY
    def test_unsafe_directory_and_lock_permissions_are_rejected(self, tmp_path):
        config = tmp_path / 'named.conf'
        config.write_text('options {};\n')
        state = tmp_path / 'state/owned.json'
        state.parent.mkdir(mode=0o777)
        state.parent.chmod(0o777)
        helper = PrivateDnssec(config, state)
        with pytest.raises(ValueError, match='directory'):
            helper.reconcile()
        state.parent.chmod(0o700)
        lock = state.parent / 'dnssec.lock'
        lock.write_text('')
        lock.chmod(0o644)
        with pytest.raises(ValueError, match='lock permissions'):
            helper.reconcile()

    @ROOT_ONLY
    def test_atomic_write_failure_keeps_previous_state_and_cleans_temporary(self, tmp_path, monkeypatch):
        state = tmp_path / 'owned.json'
        helper = PrivateDnssec(state=state)
        helper.save(['old.test'])
        def fail(*args):
            raise OSError('Interrupted write')
        monkeypatch.setattr(module.os, 'replace', fail)
        with pytest.raises(OSError):
            helper.save(['new.test'])
        assert helper.owned() == ['old.test']
        assert sorted(path.name for path in tmp_path.iterdir()) == ['owned.json']

    def test_cli_rejects_non_root_invalid_port_and_reports_failures(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(sys, 'argv', ['bind-private-dnssec'])
        monkeypatch.setattr(module.os, 'geteuid', lambda: 1000)
        with pytest.raises(SystemExit):
            module.main()
        monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
        monkeypatch.setattr(sys, 'argv', ['bind-private-dnssec', '--port', '0'])
        with pytest.raises(SystemExit):
            module.main()
        monkeypatch.setattr(sys, 'argv', ['bind-private-dnssec', '--config', str(tmp_path / 'missing')])
        assert module.main() == 1
        assert 'update failed' in capsys.readouterr().err
        monkeypatch.setattr(PrivateDnssec, 'reconcile', lambda self: {'renewed': [], 'removed': []})
        assert module.main() == 0
        assert '"renewed": []' in capsys.readouterr().out
        monkeypatch.setattr(PrivateDnssec, 'preview', lambda self: {'check': True, 'renew': [], 'remove': []})
        monkeypatch.setattr(sys, 'argv', ['bind-private-dnssec', '--check'])
        assert module.main() == 0
        assert '"check": true' in capsys.readouterr().out


@pytest.mark.parametrize('clients', [['198.51.100.0/24'], ['192.0.2.10', '10.150.1.23']])
def test_single_client_acl_preserves_dnssec_and_front_only_recursion(clients):
    """One source ACL must not broaden recursion onto BACK or weaken DNSSEC."""
    context = dict(bind_dnssec_mode='auto', bind_tsig_key_name='test-transfer', bind_tsig_algorithm='hmac-sha256',
                   bind_tsig_secret='public-render-only', bind_front_ip='192.0.2.11', bind_back_ip='10.20.1.11',
                   group_names=['dns_primary'], bind_primary_host='p', bind_secondary_host='s',
                   hostvars={'p': {'bind_front_ip': '192.0.2.11', 'bind_back_ip': '10.20.1.11'},
                             's': {'bind_front_ip': '192.0.2.12', 'bind_back_ip': '10.20.1.12'}},
                   bind_cloud_clients=clients, bind_allowed_clients=clients, bind_authoritative_zones=[],
                   bind_forward_zones=[{'domain': 'corp.test', 'dnssec_validate': False,
                                        'forwarders': [{'address': '10.11.0.53'}]},
                                       {'domain': 'signed.test', 'dnssec_validate': True,
                                        'forwarders': [{'address': '10.12.0.53'}]}])
    rendered = Environment(undefined=StrictUndefined).from_string((ROLE / 'templates/named.conf.j2').read_text()).render(**context)
    assert 'acl "allowed_clients"' in rendered
    assert 'dnssec-validation auto;' in rendered and 'listen-on-v6 { none; };' in rendered
    assert '// Bind9-Ansible DNSSEC-PRIVATE corp.test' in rendered
    assert '// Bind9-Ansible DNSSEC-PRIVATE signed.test' not in rendered
    assert 'bind_primary_back_ip;' not in rendered.split('allow-recursion-on {', 1)[1].split('};', 1)[0]
    for address in clients:
        assert address + ';' in rendered


def test_start_reload_timer_and_health_order_are_integrated():
    tasks = yaml.safe_load((ROLE / 'tasks/main.yml').read_text())
    deployment = next(task for task in tasks if task['name'] == 'Apply DNS changes with rollback on failure')
    imports = [task.get('ansible.builtin.import_tasks') for task in deployment['block']]
    assert imports.index('tasks.d/dnssec_install.yml') < imports.index('tasks.d/verify.yml')
    assert imports.index('tasks.d/service.yml') < imports.index('tasks.d/dnssec_activate.yml') < imports.index('tasks.d/health.yml')
    dropin = (ROLE / 'templates/named-dnssec-override.j2').read_text()
    assert 'ExecStartPost=' in dropin and 'ExecReload=' in dropin
    runtime = (ROLE / 'templates/named-runtime-override.j2').read_text()
    assert 'ExecStart=/usr/sbin/named -4 -u named -c ${NAMEDCONF} $OPTIONS' in runtime
    assert '|| true' not in dropin
    install = (ROLE / 'tasks/tasks.d/dnssec_install.yml').read_text()
    assert 'OnUnitActiveSec=1d' in install
    assert 'named-setup-rndc.service' in (ROLE / 'tasks/tasks.d/named_runtime.yml').read_text()


@ROOT_ONLY
def test_script_cli_entrypoint_runs_real_files_without_rndc_when_policy_is_empty(tmp_path):
    config = tmp_path / 'named.conf'
    config.write_text('options {};\n')
    result = subprocess.run([sys.executable, str(SCRIPT), '--config', str(config),
                             '--state', str(tmp_path / 'state/owned.json')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'renewed': [], 'removed': []}
