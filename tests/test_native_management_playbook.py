"""Public management playbook integration with genuine ordered local workers."""
import grp
import json
import os
from pathlib import Path

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeRecordedBoundary
from native_management_runner import NativeManagementPlayRun
from test_management import CONTEXT, M
from test_native_directories import named_identity
from test_native_management_workflow import BATCH, READ, ManagementWorkflow

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')


class ManagementPlaybook:
    """Observe ordering and confidentiality while aliases share unchanged files."""

    def __init__(self, folder):
        self.folder = folder
        self.files = ManagementWorkflow(folder)
        self.hosts = {name: dict(bind_environment='test', bind_site_name='site',
            bind_front_ip='192.0.2.' + suffix, bind_back_ip='10.0.0.' + suffix)
            for name, suffix in [('localhost', '11'), ('peer', '12')]}
        self.groups = dict(site=['localhost', 'peer'], dns_primary=['localhost'], dns_secondary=['peer'],
                           dns_deployment_order=['localhost', 'peer'])

    def approvals(self):
        result = dict(bind_manage_checksums={}, bind_manage_plan_checksums={})
        for name in self.hosts:
            context = dict(CONTEXT, primary=name == 'localhost', named_gid=grp.getgrnam('named').gr_gid,
                           backup_root='/var/backups/Bind9-Ansible/bind')
            manager = M.BatchManager(BATCH, context)
            with manager.operation():
                preview = manager.run()
            result['bind_manage_checksums'][name] = preview['checksum']
            result['bind_manage_plan_checksums'][name] = preview['plan_checksum']
        return result

    def run(self, envelope, check=False, **values):
        # The container's real setup module gathers facts. These explicit test
        # inputs exercise the supported-platform guard, not vendor certification.
        variables = dict(ansible_distribution='RedHat',
            ansible_distribution_major_version='8', bind_tsig_key_name='site-transfer')
        # None denotes an omitted variable in this public-entry fixture.
        if envelope is not None:
            variables['bind_manage'] = envelope
        variables.update(values)
        with NativeRecordedBoundary(self.folder, NativeManagementPlayRun, {}) as state:
            status, result = NativeManagementPlayRun('manage.yml', variables, inventory_hosts=self.hosts,
                inventory_groups=self.groups, check_mode=check).run()
            assert json.loads(state.read_text(encoding='utf-8'))['calls'] == []
        assert {name: Path(name).read_bytes() for name in self.files.before} == self.files.before
        assert not Path('/var/backups/Bind9-Ansible').exists()
        assert not Path('/var/lib/Bind9-Ansible/.operation.lock').exists()
        return status, result


@pytest.fixture
def playbook(tmp_path):
    with NativeFilesystemScope(NativeManagementPlayRun):
        named_identity()
        yield ManagementPlaybook(tmp_path)


@pytest.mark.parametrize('batch', [False, True])
def test_native_public_management_reads_both_ordered_hosts_without_batch_preflight(playbook, batch):
    status, result = playbook.run(BATCH if batch else READ)
    assert status == 0 and not result.failed
    assert result.ok.count('Run the private management transaction') == 2
    assert 'Gather the existing platform and network facts for preflight' not in result.ok
    assert 'censored' in result.registers['bind_management_result']


@pytest.mark.parametrize('resource,name,value', [
    ('acl', 'office', '192.0.2.0/24;'),
    ('logging', 'logging', 'channel discard { null; }; category default { discard; };'),
])
@pytest.mark.skipif(not Path('/usr/sbin/named-checkconf').is_file(), reason='Changed previews require the real BIND checker')
def test_native_public_management_previews_new_resources_on_both_hosts(playbook, resource, name, value):
    request = dict(resource=resource, action='create', name=name, value=value)
    checksum = M.Configuration(M.SafeFiles()).revision()
    status, result = playbook.run(dict(request, expected_checksum=checksum), check=True)
    assert status == 0 and not result.failed
    assert result.ok.count('Run the private management transaction') == 2


@pytest.mark.parametrize('check', [False, True])
def test_native_public_management_unchanged_approved_batch_respects_preflight_and_check_mode(playbook, check):
    status, result = playbook.run(dict(BATCH, action='apply'), check=check, **playbook.approvals())
    assert status == 0 and not result.failed
    assert result.ok.count('Run the private management transaction') == (2 if check else 4)
    assert result.ok.count('Gather the existing platform and network facts for preflight') == (0 if check else 2)
    assert 'censored' in result.registers['bind_management_result']


@pytest.mark.parametrize('host', ['localhost', 'peer'])
def test_native_public_management_any_host_preflight_refusal_prevents_the_apply_play(playbook, host):
    approvals = playbook.approvals()
    approvals['bind_manage_plan_checksums'][host] = '0' * 64
    status, result = playbook.run(dict(BATCH, action='apply'), **approvals)
    assert status == 2
    assert result.failed == ['Run the private management transaction',
                             'Report the controlled management refusal without configuration content']
    assert result.ok.count('Gather the existing platform and network facts for preflight') == (1 if host == 'localhost' else 2)
    assert 'Run the independent management workflow' not in result.ok


@pytest.mark.parametrize('envelope', [None, []])
def test_native_public_management_missing_or_invalid_mapping_does_not_enter_batch_preflight(playbook, envelope):
    status, result = playbook.run(envelope)
    assert status == 2 and result.failed == ['Validate the management platform and explicit request envelope']
    assert 'Gather the existing platform and network facts for preflight' not in result.ok
    assert 'Run the private management transaction' not in result.ok
