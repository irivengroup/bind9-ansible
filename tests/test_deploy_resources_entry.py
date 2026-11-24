"""Private deployment planning uses the real engine and never writes live files."""
import errno
import importlib.util
from pathlib import Path
import runpy
import sys

import pytest

from test_management import CONF, CONTEXT, Lab, M, pytestmark as management_marks
from test_management_entry_contract import Finished

pytestmark = management_marks


@pytest.fixture
def entry(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'ansible.module_utils.bind9_management', M)
    path = Path(__file__).resolve().parents[1] / 'roles/bind9/library/bind9_deploy_resources.py'
    spec = importlib.util.spec_from_file_location('tested_deploy_resources', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    lab = Lab(tmp_path)
    monkeypatch.setattr(module, 'BatchManager', lambda request, context:
        M.BatchManager(request, context, root=tmp_path, runner=lab.runner))
    return module, lab


def execute(module, monkeypatch, operations):
    class Transport:
        params = dict(operations=operations, context=dict(CONTEXT))

        def exit_json(self, **value):
            raise Finished(False, value)

        def fail_json(self, **value):
            raise Finished(True, value)

    def initialize(**options):
        assert options['supports_check_mode'] is True
        assert options['argument_spec']['operations']['elements'] == 'dict'
        return Transport()
    monkeypatch.setattr(module, 'AnsibleModule', initialize)
    with pytest.raises(Finished) as response:
        module.main()
    return response.value


@pytest.mark.parametrize('value', ['yes', 'no'])
def test_private_plan_does_not_publish_or_reload_even_when_candidate_changes(entry, monkeypatch, value):
    module, lab = entry
    result = execute(module, monkeypatch, [dict(resource='option', action='update', name='minimal-responses', value=value)])
    assert not result.failed and result.value['changed'] is False
    assert ('minimal-responses no;' in result.value['content']) is (value == 'no')
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert len(lab.calls) == 1 and Path(lab.calls[0][0]).name == 'named-checkconf'
    assert not (lab.root / 'var/backups').exists()


@pytest.mark.parametrize('resource,name,value', [
    ('acl', 'monitoring', '192.0.2.40;'),
    ('logging', 'logging', 'channel discard { null; }; category default { discard; };')])
def test_named_block_customizations_use_the_shared_complete_candidate(entry, monkeypatch, resource, name, value):
    module, lab = entry
    result = execute(module, monkeypatch, [dict(resource=resource, action='create', name=name, value=value)])
    assert not result.failed and value in result.value['content']
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert len(lab.calls) == (2 if resource == 'acl' else 1)


@pytest.mark.parametrize('operation', [
    dict(resource='zone', action='create', name='x.test', kind='authoritative'),
    dict(resource='record', action='create', zone='x.test', name='x', type='A', values=['192.0.2.40']),
    dict(resource='option', action='update', name='allow-query', value='{ any; }'),
    dict(resource='acl', action='create', name='dns_query_clients', value='any;'),
    dict(resource='logging', action='create', name='logging', value='include "/etc/foreign";')])
def test_private_planner_refuses_scope_escape_before_any_publication(entry, monkeypatch, operation):
    module, lab = entry
    result = execute(module, monkeypatch, [operation])
    assert result.failed and (lab.root / 'etc/named.conf').read_text() == CONF
    assert not lab.calls


@pytest.mark.parametrize('fault', ['unowned-path', 'concurrent-edit'])
def test_candidate_scope_and_observed_revision_are_rechecked(entry, monkeypatch, fault):
    module, lab = entry
    construct = module.BatchManager
    def planner(request, context):
        manager = construct(request, context)
        validate = manager.validate
        def validated():
            validate()
            if fault == 'unowned-path':
                manager.changes['/etc/foreign'] = b'foreign'
            else:
                (lab.root / 'etc/named.conf').write_text(CONF + '// concurrent operator\n')
        manager.validate = validated
        return manager
    monkeypatch.setattr(module, 'BatchManager', planner)
    result = execute(module, monkeypatch, [dict(resource='option', action='update', name='minimal-responses', value='no')])
    assert result.failed
    assert ('another asset' in result.value['msg']) is (fault == 'unowned-path')
    assert not (lab.root / 'etc/foreign').exists()
    assert (lab.root / 'etc/named.conf').read_text() == CONF + ('// concurrent operator\n' if fault == 'concurrent-edit' else '')


@pytest.mark.parametrize('error,expected', [(OSError(errno.EACCES, 'SECRET /private'), 'EACCES'),
    (OSError(987654, 'SECRET /private'), 'UNKNOWN'), (RuntimeError('SECRET /private'), 'unsafe or unavailable')])
def test_private_transport_errors_do_not_disclose_configuration(entry, monkeypatch, error, expected):
    module, _ = entry
    def refuse(*args):
        raise error
    monkeypatch.setattr(module, 'BatchManager', refuse)
    result = execute(module, monkeypatch, [])
    assert result.failed and expected in result.value['msg']
    assert 'SECRET' not in result.value['msg'] and '/private' not in result.value['msg']


def test_private_module_script_initializes_native_transport(monkeypatch):
    import ansible.module_utils.basic as basic
    monkeypatch.setitem(sys.modules, 'ansible.module_utils.bind9_management', M)
    def initialize(**options):
        assert options['argument_spec']['context']['required'] is True
        raise Finished(False, {'initialized': True})
    monkeypatch.setattr(basic, 'AnsibleModule', initialize)
    path = Path(__file__).resolve().parents[1] / 'roles/bind9/library/bind9_deploy_resources.py'
    with pytest.raises(Finished) as response:
        runpy.run_path(str(path), run_name='__main__')
    assert response.value.value == {'initialized': True}
