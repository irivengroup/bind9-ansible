"""Regressions for strict inputs, exact DNS probes and operation ownership."""
import pytest

from test_management import CONF, CONTEXT, Lab, M, ZONE

pytestmark = pytest.mark.skipif(M.os.name != 'posix' or M.os.geteuid() != 0,
                                reason='Requires isolated Linux root')


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


@pytest.mark.parametrize('payload', [None, [], 'request', {1: 'secret'},
    dict(resource='option', action='read', name=[]),
    dict(resource='option', action='read', name='bad;name')])
def test_malformed_request_has_a_controlled_refusal(lab, payload):
    with pytest.raises(M.ManagementError):
        M.Manager(payload, CONTEXT, root=lab.root).run()
    assert (lab.root / 'etc/named.conf').read_text() == CONF


@pytest.mark.parametrize('address', [True, 167772213, None, [], {}])
def test_forwarder_address_must_be_an_explicit_ip_string(lab, address):
    with pytest.raises(M.ManagementError, match='forwarder'):
        lab.write('zone', 'create', 'private.test', kind='forward',
                  forwarders=[{'address': address}])
    assert not lab.calls
    assert (lab.root / 'etc/named.conf').read_text() == CONF


@pytest.mark.parametrize('address', ['255.255.255.255', '::ffff:255.255.255.255',
    '::ffff:224.0.0.1', '::ffff:0.0.0.0', '::ffff:169.254.0.53'])
def test_mapped_addresses_cannot_bypass_forwarder_unicast_policy(lab, address):
    with pytest.raises(M.ManagementError, match='forwarder'):
        lab.write('zone', 'create', 'private.test', kind='forward',
                  forwarders=[{'address': address}])
    assert not lab.calls


@pytest.mark.parametrize('answer', [
    ';; status: NOERROR\nother.test. 60 IN A 127.0.0.1\n',
    ';; status: SERVFAIL\nlocalhost. 60 IN TXT "status: NOERROR"\nlocalhost. 60 IN A 127.0.0.1\n',
])
def test_localhost_probe_cannot_accept_an_unrelated_answer(lab, monkeypatch, answer):
    manager = lab.manager()
    manager.config = M.Configuration(manager.files)
    manager.runner = lambda argv: answer
    monkeypatch.setattr(M.time, 'sleep', lambda seconds: None)
    with pytest.raises(M.ManagementError, match='health'):
        manager.health()


def test_soa_probe_requires_the_exact_zone_owner(lab, monkeypatch):
    manager = lab.manager('record', 'update', 'www', zone='example.test', type='A')
    manager.config = M.Configuration(manager.files)
    manager.changes['/var/named/zones/example.test.zone'] = M.ZoneDocument(ZONE, 'example.test.').bump().encode()
    original = lab.runner
    manager.runner = lambda argv: (';; status: NOERROR\n;; flags: qr aa;\n'
        'other.test. 300 IN SOA ns1.example.test. hostmaster.example.test. 42 3600 900 604800 300\n'
        if argv[-1] == 'SOA' else original(argv))
    monkeypatch.setattr(M.time, 'sleep', lambda seconds: None)
    with pytest.raises(M.ManagementError, match='health'):
        manager.health()


def test_replaced_lease_is_preserved_for_operator_recovery(lab):
    manager = lab.manager()
    lease = lab.root / 'var/lib/Bind9-Ansible/.operation.lock'
    with pytest.raises(M.ManagementError, match='lease'):
        with manager.lease():
            lease.rename(lease.with_name('original-lease'))
            lease.mkdir(mode=0o700)
    assert lease.is_dir()
    assert lease.with_name('original-lease').is_dir()


@pytest.mark.parametrize('native', [False, True])
def test_read_and_check_hold_the_operation_lease(tmp_path, native):
    # The actual Ansible transport must own the lease before reading its graph.
    from test_management_ansible import Runner
    runner = Runner(tmp_path, native)
    utility = runner.role / 'module_utils/bind9_management.py'
    utility.write_text(utility.read_text().replace(
        'self.config = Configuration(self.files)',
        "if not (self.root / 'var/lib/Bind9-Ansible/.operation.lock').is_dir():\n"
        "            raise ManagementError('Observation is not serialized')\n"
        '        self.config = Configuration(self.files)'))
    request = dict(resource='option', action='read', name='minimal-responses')
    tasks = [runner.role_task('tasks.d/manage/main.yml'),
        {'set_fact': {'bind_manage': dict(request, action='update', value='no',
                        expected_checksum='{{ bind_management_result.checksum }}')}},
        {'block': [runner.role_task('tasks.d/manage/main.yml')], 'check_mode': True}]
    runner.run(tasks, {'bind_manage': request})
    assert (runner.lab.root / 'etc/named.conf').read_text() == CONF
    assert not (runner.lab.root / 'var/lib/Bind9-Ansible/.operation.lock').exists()


@pytest.mark.parametrize('name,kind,expected,answer,accepted', [
    ('localhost.', 'A', '127.0.0.1', 'LOCALHOST. 60 IN A 127.0.0.1', True),
    ('localhost.', 'A', '127.0.0.1', 'localhost. 60 IN A 127.0.0.2', False),
    ('example.test.', 'SOA', '42', 'example.test. 60 IN SOA ns. host. 42 1 2 3 4', True),
    ('example.test.', 'SOA', '42', 'example.test. 60 IN SOA ns. host. 420 1 2 3 4', False),
    ('example.test.', 'SOA', '*', 'example.test. 60 IN SOA ns. host. 9 1 2 3 4', True),
    ('example.test.', 'SOA', None, 'example.test. 60 IN SOA ns. host. 9 1 2 3 4', True),
    ('example.test.', 'SOA', None, 'example.test. 60 IN SOA ns. host. x 1 2 3 4', False),
])
def test_dns_reply_accepts_only_the_expected_answer(name, kind, expected, answer, accepted):
    reply = M.DnsReply(';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n;; flags: qr aa;\n' + answer)
    assert reply.matches(name, kind, expected) is accepted


def test_non_authoritative_soa_cannot_prove_a_local_zone_is_loaded():
    reply = M.DnsReply(';; status: NOERROR\nexample.test. 60 IN SOA ns. host. 42 1 2 3 4\n')
    assert not reply.matches('example.test.', 'SOA', '42')
    assert reply.matches('example.test.', 'SOA', None)


def test_missing_lease_is_reported_without_claiming_ownership(lab):
    with pytest.raises(M.ManagementError, match='lease disappeared'):
        with lab.manager().lease():
            (lab.root / 'var/lib/Bind9-Ansible/.operation.lock').rmdir()


@pytest.mark.parametrize('entry', ['directory', 'file', 'dangling-link'])
@pytest.mark.parametrize('check_mode', [False, True])
def test_read_and_simulation_refuse_any_existing_lease(tmp_path, entry, check_mode):
    from test_management_ansible import Runner
    runner = Runner(tmp_path, False)
    state = runner.lab.root / 'var/lib/Bind9-Ansible'
    state.mkdir(parents=True, mode=0o700)
    lease = state / '.operation.lock'
    if entry == 'directory':
        lease.mkdir(mode=0o700)
    elif entry == 'file':
        lease.write_text('retain for recovery')
    else:
        lease.symlink_to(state / 'absent')
    request = dict(resource='option', action='read', name='minimal-responses')
    if check_mode:
        request.update(action='update', value='no', expected_checksum=runner.lab.manager().run()['checksum'])
    tasks = [{'block': [runner.role_task('tasks.d/manage/main.yml'),
                       {'fail': {'msg': 'busy lease was accepted'}}],
              'rescue': [{'assert': {'that': ["'owns the host lease' in ansible_failed_result.msg"]}}],
              'check_mode': check_mode}]
    runner.run(tasks, {'bind_manage': request})
    assert lease.lstat()
    assert (runner.lab.root / 'etc/named.conf').read_text() == CONF
