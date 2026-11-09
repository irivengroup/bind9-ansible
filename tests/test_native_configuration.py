"""Canonical configuration publication, permissions, notifications and repeats.

The native template/file/systemd modules run in a disconnected disposable root.
Only systemctl has a shared CLI boundary; these cases make no daemon claim.
"""
import json
import os
from pathlib import Path
import re

import pytest

from native_ansible_runner import NativeConfigurationRun, NativeFilesystemScope, NativeUnitBoundary
from test_native_directories import named_identity
from test_native_preflight import scope

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')


@pytest.fixture
def configuration(tmp_path):
    with NativeFilesystemScope(NativeConfigurationRun):
        _, gid = named_identity()
        with NativeUnitBoundary(tmp_path, {'named': {'active': True, 'enabled': True}}) as state:
            yield gid, state


@pytest.mark.parametrize('secondary', [False, True])
@pytest.mark.parametrize('dnssec,expected', [('auto', 'auto'), (True, 'yes'), (False, 'no')])
@pytest.mark.parametrize('populated', [False, True])
def test_native_configuration_publication_and_second_run_are_idempotent(configuration, secondary, dnssec, expected, populated):
    gid, state = configuration
    hosts, groups = scope()
    values = hosts['localhost']
    values['bind_dnssec_active'] = dnssec
    # Exercise the native private-forwarding branch as well as the default
    # validated forwarder; the global validation policy remains unchanged.
    values['bind_forward_zones'][0]['dnssec_validate'] = dnssec is not True
    if secondary:
        groups = {'dns_primary': ['peer'], 'dns_secondary': ['localhost']}
        values.update(bind_primary_host='peer', bind_secondary_host='localhost')
    if populated:
        values.update(bind_cloud_clients=['203.0.113.0/25'], bind_internal_clients=['203.0.113.128/25'])
    else:
        values.update(bind_allowed_clients=[], bind_authoritative_zones=[], bind_managed_zones=[], bind_forward_zones=[])
    retired = Path('/usr/local/sbin/named-transaction')
    retired.write_text('legacy fixture executable\n', encoding='utf-8')
    retired.chmod(0o700)
    target = Path('/etc/named.conf')
    fingerprints = []
    for iteration in range(2):
        status, result = NativeConfigurationRun('tasks.d/deploy/configuration.yml', {},
            inventory_hosts=hosts, inventory_groups=groups).run()
        assert status == 0 and not result.failed
        assert not retired.exists()
        rendered = target.read_text(encoding='utf-8')
        info = target.stat()
        assert info.st_uid == 0 and info.st_gid == gid and info.st_mode & 0o777 == 0o640
        assert 'dnssec-validation ' + expected + ';' in rendered
        assert 'listen-on-v6 { none; };' in rendered
        assert 'include "/etc/rndc.key";' in rendered
        if populated:
            managed = re.search(r'zone "example[.]test" IN \{(.*?)\n\};', rendered, re.S).group(1)
            assert ('type slave;' if secondary else 'type master;') in managed
        assert 'zone "." IN {\n    type master;' in rendered
        assert ('zone "example.test"' in rendered) is populated
        assert ('acl "user_defined_clients"' in rendered) is populated
        assert ('zone "upstream.test"' in rendered) is populated
        assert ('// Bind9-Ansible DNSSEC-PRIVATE upstream.test' in rendered) is (populated and dnssec is True)
        prefix = 'bind_secondary_' if secondary else 'bind_primary_'
        expected_destinations = ['local_loopback;']
        if populated:
            expected_destinations += [prefix + 'front_ip;', prefix + 'back_ip;']
        for directive in ('allow-recursion-on', 'allow-query-cache-on'):
            body = re.search(directive + r'\s*\{([^{}]*)\};', rendered).group(1).split()
            assert body == expected_destinations
        assert ('Reload named configuration' in result.ok) is (iteration == 0)
        fingerprints.append((target.read_bytes(), info.st_mtime_ns, info.st_ino))
    assert fingerprints[0] == fingerprints[1]
    operations = json.loads(state.read_text(encoding='utf-8'))['operations']
    assert operations.count({'daemon_reload': True}) == 1
    assert operations.count({'name': 'named', 'state': 'reloaded'}) == 1
