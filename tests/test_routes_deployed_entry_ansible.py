"""Exercise only the public routes tag with the real shared guard and role entry.

The host already has a completion seal and operator DNS data. NetworkManager
and iproute2 use the existing isolated CLI doubles; no BIND task is substituted.
"""
import pytest

from test_front_routes_ansible import AnsibleRouteSandbox, BASE, NEW, pytestmark as inherited_pytestmark

pytestmark = inherited_pytestmark


@pytest.mark.parametrize('native', [False, True])
def test_routes_on_a_deployed_host_ignore_a_previous_lease_without_changing_dns(tmp_path, native):
    """The temporary default bypasses only the shared lease, even with a seal."""
    sandbox = AnsibleRouteSandbox(tmp_path)
    sandbox.env['ANSIBLE_JINJA2_NATIVE'] = str(native).lower()
    sandbox.inventory.write_text(
        '[dns_primary]\nlocalhost ansible_connection=local ansible_host=192.0.2.2 '
        'bind_front_ip=198.51.100.2 bind_back_ip=10.0.0.2\n'
        '[dns_secondary]\nsecondary bind_front_ip=198.51.100.3 bind_back_ip=10.0.0.3\n'
        '[site1]\nlocalhost\nsecondary\n'
        '[site1:vars]\nbind_environment=test\nbind_site_name=site1\n')
    role = tmp_path / 'roles/bind9'
    live = tmp_path / 'dns/named.conf'
    live.parent.mkdir()
    live.write_bytes(b'// Existing operator configuration must remain byte-identical.\n')
    # Keep production role imports, tags and acquisition/release unchanged.
    # Rebase privileged paths only, as in the existing deployment transport lab.
    for relative in ('tasks/main.yml', 'tasks/tasks.d/deploy/guard.yml',
                     'tasks/tasks.d/deploy/lease_release.yml'):
        path = role / relative
        path.write_text(path.read_text().replace('/var/lib/Bind9-Ansible', str(sandbox.state_root)))
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
    assert lease.stat().st_ino == lease_identity and not (sandbox.state_root / 'front-routes.lock').exists()

    applied = sandbox.run(tasks_from='main.yml', tags='routes')
    assert applied.returncode == 0, applied.stdout
    first = sandbox.read()
    assert first['runtime'] == BASE + [NEW]
    assert len(first['persistent']) == 1 and len(first['operations']) == 2
    assert lease.stat().st_ino == lease_identity and not (sandbox.state_root / 'front-routes.lock').exists()

    repeated = sandbox.run(tasks_from='main.yml', tags='routes')
    assert repeated.returncode == 0, repeated.stdout
    assert sandbox.read()['operations'] == first['operations']
    assert 'changed=0' in repeated.stdout
    assert (seal.read_bytes(), seal.stat().st_ino, seal.stat().st_mtime_ns) == seal_before
    assert (live.read_bytes(), live.stat().st_ino, live.stat().st_mtime_ns) == dns_before
    assert lease.stat().st_ino == lease_identity and not (sandbox.state_root / 'front-routes.lock').exists()
    assert evidence.read_bytes() == b'preserve prior lease'
    forbidden = ('Install canonical named.conf', 'Install authoritative zone files',
                 'Install BIND and SELinux tooling', 'Start or reload BIND')
    assert not any(name in applied.stdout or name in repeated.stdout for name in forbidden)
