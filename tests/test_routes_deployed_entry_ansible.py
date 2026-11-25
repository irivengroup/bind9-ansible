"""Exercise only the public routes tag with the real shared guard and role entry.

The host already has a completion seal and operator DNS data. NetworkManager
and iproute2 use the existing isolated CLI doubles; no BIND task is substituted.
"""
import pytest
import re
import yaml

from test_dns_network import link, write_ip_probe
from test_front_routes_ansible import AnsibleRouteSandbox, BASE, NEW, pytestmark as inherited_pytestmark

pytestmark = inherited_pytestmark


def public_entry_sandbox(folder, front_prefix='198.51.100.'):
    """Share inventory and private lease paths; retain production import/tag flow."""
    sandbox = AnsibleRouteSandbox(folder)
    sandbox.inventory = folder / 'hosts.yml'
    hosts = {name: dict(ansible_connection='local', ansible_host='192.0.2.' + suffix,
        bind_listen_interfaces=[dict(name='front0', ipaddress=front_prefix + suffix, scope='front'),
                                dict(name='back0', ipaddress='10.0.0.' + suffix, scope='back')])
        for name,suffix in [('localhost','2'),('secondary','3')]}
    sandbox.inventory.write_text(yaml.safe_dump(dict(all=dict(children={
        'dns_primary':dict(hosts=dict(localhost={})),
        'dns_secondary':dict(hosts=dict(secondary={})),
        'site1':dict(vars=dict(bind_environment='test',bind_site_name='site1'),hosts=hosts)}))))
    write_ip_probe(folder / 'bin', {'': [
        link('front0', front_prefix + '2', front_prefix + '3'),
        link('back0', '10.0.0.2', '10.0.0.3'),
        link('adm0', '192.0.2.2', '192.0.2.3'),
    ]}, existing=True)
    role = folder / 'roles/bind9'
    # Keep production role imports, tags and acquisition/release unchanged.
    # Rebase privileged paths only, as in the existing deployment transport lab.
    for relative in ('tasks/main.yml', 'tasks/tasks.d/deploy/guard.yml',
                     'tasks/tasks.d/deploy/lease_release.yml'):
        path = role / relative
        path.write_text(path.read_text().replace('/var/lib/Bind9-Ansible', str(sandbox.state_root)))
    return sandbox


def apply_pending_transaction(sandbox):
    """Seed a checked pending journal using the previous invocation's resolved facts."""
    canonical = sandbox.inventory.read_bytes()
    inventory = yaml.safe_load(canonical)
    for host in inventory['all']['children']['site1']['hosts'].values():
        for interface in host['bind_listen_interfaces']:
            host['bind_' + interface['scope'] + '_ip'] = interface['ipaddress']
    sandbox.inventory.write_text(yaml.safe_dump(inventory))
    try:
        return sandbox.run(extra_vars={'bind_front_routes_defer_commit': True})
    finally:
        # A separate real recovery invocation has only the canonical inventory,
        # not ephemeral address facts from the earlier successful transaction.
        sandbox.inventory.write_bytes(canonical)


@pytest.mark.parametrize('dns_input', ['resources', 'clients', 'dnssec'])
def test_recovery_uses_saved_route_state_without_current_dns_context(tmp_path, dns_input):
    """Recovery needs its checked journal, even without derived DNS address facts."""
    sandbox = public_entry_sandbox(tmp_path)
    applied = apply_pending_transaction(sandbox)
    assert applied.returncode == 0, applied.stdout
    journal = sandbox.state_root / 'front-routes.pending'
    assert journal.is_dir() and sandbox.read()['runtime'] == BASE + [NEW]
    # A valid nonempty desired DNS state requires address facts when compiled.
    # Route recovery must use its journal while retaining the helper's own
    # mandatory kernel/admin safety observations.
    request = dict(resource='zone', action='create', kind='authoritative', name='retained.example.test')
    current_inputs = {
        'resources': {'bind_resources_deploy': request},
        'clients': {'bind_trusted_networks': {'cloud_clients': [], 'internal_clients': []}},
        'dnssec': {'bind_dnssec_active': 'invalid'},
    }
    recovered = sandbox.run(tasks_from='main.yml', tags='routes-recover',
                            extra_vars=current_inputs[dns_input])
    assert recovered.returncode == 0, recovered.stdout
    observation = re.search(r'TASK \[bind9 : Read active IPv4 interfaces from the kernel of each peer\].*?\n(.*?)(?=\nTASK|\Z)',
                            recovered.stdout, re.DOTALL).group(1)
    assert observation.count('skipping: [localhost] => (item=') == 2
    assert 'ok: [localhost]' not in observation
    assert not journal.exists()
    assert sandbox.read()['runtime'] == BASE and sandbox.read()['persistent'] == []


@pytest.mark.parametrize('native', [False, True])
def test_routes_on_a_deployed_host_ignore_a_previous_lease_without_changing_dns(tmp_path, native):
    """The temporary default bypasses only the shared lease, even with a seal."""
    sandbox = public_entry_sandbox(tmp_path)
    sandbox.env['ANSIBLE_JINJA2_NATIVE'] = str(native).lower()
    role = tmp_path / 'roles/bind9'
    live = tmp_path / 'dns/named.conf'
    live.parent.mkdir()
    live.write_bytes(b'// Existing operator configuration must remain byte-identical.\n')
    for path in (role / 'tasks').rglob('*.yml'):
        path.write_text(path.read_text().replace('/etc/named.conf', str(live)))
    sandbox.state_root.mkdir(mode=0o700)
    seal = sandbox.state_root / '.deployment.lock'
    seal.write_bytes(b'existing completion seal\n')
    seal.chmod(0o600)
    seal_before = seal.read_bytes(), seal.stat().st_ino, seal.stat().st_mtime_ns
    dns_before = live.read_bytes(), live.stat().st_ino, live.stat().st_mtime_ns
    lease = sandbox.state_root / '.operation.lock'
    lease.mkdir(mode=0o700)
    evidence = lease / 'operator-evidence'
    evidence.write_bytes(b'preserve prior lease')
    lease_identity = lease.stat().st_ino

    preview = sandbox.run(tasks_from='main.yml', tags='routes', check=True)
    assert preview.returncode == 0, preview.stdout
    assert sandbox.read() == sandbox.data
    assert lease.stat().st_ino == lease_identity and not (sandbox.state_root / 'front-routes.pending').exists()

    applied = sandbox.run(tasks_from='main.yml', tags='routes')
    assert applied.returncode == 0, applied.stdout
    first = sandbox.read()
    assert first['runtime'] == BASE + [NEW]
    assert len(first['persistent']) == 1 and len(first['operations']) == 2
    assert lease.stat().st_ino == lease_identity and not (sandbox.state_root / 'front-routes.pending').exists()

    repeated = sandbox.run(tasks_from='main.yml', tags='routes')
    assert repeated.returncode == 0, repeated.stdout
    assert sandbox.read()['operations'] == first['operations']
    assert 'changed=0' in repeated.stdout
    assert (seal.read_bytes(), seal.stat().st_ino, seal.stat().st_mtime_ns) == seal_before
    assert (live.read_bytes(), live.stat().st_ino, live.stat().st_mtime_ns) == dns_before
    assert lease.stat().st_ino == lease_identity and not (sandbox.state_root / 'front-routes.pending').exists()
    assert evidence.read_bytes() == b'preserve prior lease'
    assert not (sandbox.state_root / 'front-routes.pending').exists()
    forbidden = ('Install canonical named.conf', 'Install authoritative zone files',
                 'Install BIND and SELinux tooling', 'Start or reload BIND')
    assert not any(name in applied.stdout or name in repeated.stdout for name in forbidden)


@pytest.mark.parametrize('tags', ['routes', 'deploy', 'preflight', 'routes-recover,routes', 'routes-recover,deploy'])
def test_dns_input_checks_remain_mandatory_outside_exclusive_recovery(tmp_path, tags):
    """Adding a recovery selector must not authorize DNS changes with invalid clients."""
    sandbox = public_entry_sandbox(tmp_path)
    before = sandbox.read()
    result = sandbox.run(tasks_from='main.yml', tags=tags,
        extra_vars={'bind_trusted_networks': {'cloud_clients': [], 'internal_clients': []}})
    assert result.returncode != 0
    assert 'cannot both be empty' in result.stdout
    assert 'Select the explicit FRONT action without altering the DNS workflow' not in result.stdout
    assert sandbox.read() == before
    assert not (sandbox.state_root / 'front-routes.pending').exists()


def test_reserved_never_tag_cannot_roll_back_an_interrupted_front_transaction(tmp_path):
    """The public entry must retain the journal until routes-recover is explicit."""
    sandbox = public_entry_sandbox(tmp_path)
    applied = apply_pending_transaction(sandbox)
    assert applied.returncode == 0, applied.stdout
    journal = sandbox.state_root / 'front-routes.pending'
    identity = journal.stat().st_ino
    before = {path.name: path.read_bytes() for path in journal.iterdir()}
    network = sandbox.read()
    assert network['runtime'] == BASE + [NEW] and len(network['persistent']) == 1

    ignored = sandbox.run(tasks_from='main.yml', tags='never')
    assert ignored.returncode == 0, ignored.stdout
    assert journal.is_dir(), 'The reserved never selector must not authorize network rollback'
    assert journal.stat().st_ino == identity
    assert {path.name: path.read_bytes() for path in journal.iterdir()} == before
    assert sandbox.read() == network

    recovered = sandbox.run(tasks_from='main.yml', tags='routes-recover')
    assert recovered.returncode == 0, recovered.stdout
    assert not journal.exists()
    assert sandbox.read()['runtime'] == BASE and sandbox.read()['persistent'] == []
