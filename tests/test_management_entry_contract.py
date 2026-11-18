"""Exercise canonical Ansible entry code with the real isolated transaction engine.

Native transport is covered separately. Here only Ansible's stdin/stdout and
the named group lookup are replaced; configuration/locking/planning stay real.
"""
import errno
import importlib.util
import os
from pathlib import Path
import sys
import runpy
from types import SimpleNamespace

import pytest

from test_management import CONF, CONTEXT, Lab, M


class Finished(BaseException):
    """Ansible exits the interpreter after serializing exactly one response."""

    def __init__(self, failed, value):
        self.failed, self.value = failed, value


class Transport:
    def __init__(self, request, context, lock, check):
        self.params = dict(request=request, context=context, operation_lock=lock)
        self.check_mode = check

    def exit_json(self, **value):
        raise Finished(False, value)

    def fail_json(self, **value):
        raise Finished(True, value)


@pytest.fixture
def entry(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'ansible.module_utils.bind9_management', M)
    path = Path(__file__).resolve().parents[1] / 'roles/bind9/library/bind9_manage.py'
    spec = importlib.util.spec_from_file_location('tested_management_entry', path)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    lab = Lab(tmp_path)
    monkeypatch.setattr(entry.grp, 'getgrnam', lambda name: SimpleNamespace(gr_gid=0))
    for name in ('Manager', 'BatchManager'):
        implementation = getattr(M, name)
        def construct(request, context, check, implementation=implementation):
            return implementation(request, context, check=check, root=tmp_path, runner=lab.runner)
        monkeypatch.setattr(entry, name, construct)
    return entry, lab


def execute(entry, monkeypatch, request, lock=True, check=False):
    def constructor(**options):
        assert options['supports_check_mode'] is True
        assert options['argument_spec']['request']['required'] is True
        return Transport(request, dict(CONTEXT), lock, check)
    monkeypatch.setattr(entry, 'AnsibleModule', constructor)
    with pytest.raises(Finished) as response:
        entry.main()
    return response.value


@pytest.mark.parametrize('lock', [False, True])
@pytest.mark.parametrize('batch', [False, True])
@pytest.mark.parametrize('check', [False, True])
@pytest.mark.skipif(os.geteuid() != 0, reason='Real management snapshots require a root-owned isolated deployment')
def test_entry_uses_real_management_plans_and_check_mode(entry, monkeypatch, lock, batch, check):
    module, lab = entry
    request = dict(resource='batch', action='read', operations=[dict(resource='option', action='update',
                   name='minimal-responses', value='no')]) if batch else dict(resource='option', action='read', name='minimal-responses')
    response = execute(module, monkeypatch, request, lock, check)
    assert not response.failed
    assert response.value['changed'] is False
    assert ('plan_checksum' in response.value) is batch
    if not batch:
        assert response.value['value'] == 'yes'
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert not (lab.root / 'var/backups').exists()


def test_controlled_management_refusal_retains_its_safe_diagnostic(entry, monkeypatch):
    module, _ = entry
    response = execute(module, monkeypatch, dict(resource='unknown', action='read'))
    assert response.failed
    assert 'resource' in response.value['msg'].lower()


@pytest.mark.parametrize('error,expected', [
    (OSError(errno.EACCES, 'SECRET /private/path'), 'EACCES'),
    (OSError(987654, 'SECRET /private/path'), 'UNKNOWN'),
    (RuntimeError('SECRET /private/path'), 'unsafe, unsupported or unavailable'),
])
def test_boundary_errors_never_serialize_paths_native_output_or_secrets(entry, monkeypatch, error, expected):
    module, _ = entry
    def failure(*args, **kwargs):
        raise error
    monkeypatch.setattr(module, 'Manager', failure)
    response = execute(module, monkeypatch, dict(resource='option', action='read', name='minimal-responses'))
    assert response.failed
    assert expected in response.value['msg']
    assert 'SECRET' not in response.value['msg'] and '/private/path' not in response.value['msg']


def test_installed_module_entry_invokes_ansible_without_touching_host_state(monkeypatch):
    import ansible.module_utils.basic as basic
    monkeypatch.setitem(sys.modules, 'ansible.module_utils.bind9_management', M)
    def initialize(**options):
        assert options['supports_check_mode'] is True
        raise Finished(False, {'initialized': True})
    monkeypatch.setattr(basic, 'AnsibleModule', initialize)
    path = Path(__file__).resolve().parents[1] / 'roles/bind9/library/bind9_manage.py'
    with pytest.raises(Finished) as response:
        runpy.run_path(str(path), run_name='__main__')
    assert response.value.value == {'initialized': True}
