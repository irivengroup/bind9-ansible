"""Exercise file logging, rotation and query silence with an unprivileged BIND."""
import argparse
import copy
import json
import os
from pathlib import Path
import pwd
import socket
import subprocess
import tempfile
import time

from integration_loopback import LoopbackIntegration


class LoggingIntegration(LoopbackIntegration):
    """Use production thresholds and channels; rebase only sandbox locations."""

    def run(self):
        account = pwd.getpwnam('nobody')
        _, context = next(self.contexts())
        context = copy.deepcopy(context)
        context.update(bind_dnssec_active=False, bind_front_ip='127.0.0.2', bind_back_ip='127.0.0.3',
                       group_names=[context['bind_primary_group']], bind_cloud_clients=['127.0.0.10'],
                       bind_internal_clients=[], bind_forward_zones=[])
        context['hostvars'][context['bind_primary_host']].update(bind_front_ip='127.0.0.2', bind_back_ip='127.0.0.3')
        context['hostvars'][context['bind_secondary_host']].update(bind_front_ip='127.0.0.4', bind_back_ip='127.0.0.5')
        with tempfile.TemporaryDirectory(prefix='bind-logging-') as temp:
            folder = Path(temp)
            folder.chmod(0o711)
            sandbox = folder / 'server'
            config = self.render(sandbox, context)
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            self.sandbox_config(config, port)
            logs = sandbox / 'logs'
            for name in ('default.log', 'security.log', 'xfer.log'):
                path = logs / name
                path.write_text('')
                path.chmod(0o640)
            # Exceed the actual configured 10 MiB threshold and seed the full
            # archive set: startup must rotate and discard the oldest version.
            (logs / 'default.log').write_bytes(b'x' * (10 * 1024 * 1024 + 1))
            for number in range(5):
                (logs / ('default.log.' + str(number))).write_text('archive-' + str(number))
            key = sandbox / 'transfer.key'
            key.write_text('key "' + context['bind_tsig_key_name'] + '" { algorithm hmac-sha256; secret "' + context['bind_tsig_secret'] + '"; };\n')
            key.chmod(0o600)
            for path in [sandbox] + list(sandbox.rglob('*')):
                os.chown(path, account.pw_uid, account.pw_gid)
            config.chmod(0o640)
            with (folder / 'startup.log').open('w+') as startup:
                # -g redirects channels to stderr; use -f to test real files.
                process = subprocess.Popen([str(self.named), '-f', '-u', account.pw_name, '-c', str(config),
                                            '-n', '1', '-U', '1'], stdout=startup, stderr=startup)
                try:
                    for _ in range(100):
                        if process.poll() is not None:
                            startup.seek(0)
                            raise AssertionError(startup.read())
                        try:
                            with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                                break
                        except OSError:
                            time.sleep(0.1)
                    else:
                        raise AssertionError('File-logging BIND did not start')
                    dig = [str(self.bin_dir / 'dig'), '-p', str(port), '+time=3', '+tries=1']
                    refused = subprocess.check_output(dig + ['-b', '127.0.0.9', '@127.0.0.1', 'localhost', 'A'], universal_newlines=True)
                    assert 'status: REFUSED' in refused, refused
                    zone = context['bind_managed_zones'][0]['domain']
                    transfer = subprocess.check_output(dig + ['-b', '127.0.0.5', '@127.0.0.3', '-k', str(key), zone, 'AXFR'], universal_newlines=True)
                    assert transfer.count(' IN SOA ') == 2 or transfer.count('\tSOA\t') == 2, transfer
                    probe = 'quiet-logging-probe.example.invalid'
                    answer = subprocess.check_output(dig + ['-b', '127.0.0.1', '@127.0.0.1', probe, 'A'], universal_newlines=True)
                    assert 'status: NXDOMAIN' in answer, answer
                    status = subprocess.check_output([str(self.bin_dir / 'rndc'), '-s', '127.0.0.1', '-p',
                                                      str(self.control_ports[config]), '-k', str(sandbox / 'rndc.key'), 'status'], universal_newlines=True)
                    assert 'query logging is OFF' in status, status
                    for _ in range(30):
                        records = {name: (logs / name).read_text() for name in ('default.log', 'security.log', 'xfer.log')}
                        if 'running' in records['default.log'] and 'denied' in records['security.log'] and 'AXFR' in records['xfer.log']:
                            break
                        time.sleep(0.1)
                    assert 'running' in records['default.log'], records
                    assert 'denied' in records['security.log'], records
                    assert 'AXFR' in records['xfer.log'], records
                    assert all(probe not in text for text in records.values()), records
                    assert (logs / 'default.log.0').stat().st_size > 10 * 1024 * 1024
                    assert (logs / 'default.log.4').read_text() == 'archive-3'
                    assert not (logs / 'default.log.5').exists()
                    assert all((logs / name).stat().st_uid == account.pw_uid for name in records)
                    modes = {name: oct((logs / name).stat().st_mode & 0o777) for name in records}
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
        print(json.dumps({'files_written': 3, 'unprivileged_daemon': True, 'actual_rotation_threshold_bytes': 10 * 1024 * 1024,
                          'backup_versions': 5, 'oldest_archive_removed': True, 'query_logging_disabled': True,
                          'security_refusal_logged': True, 'tsig_transfer_logged': True, 'dns_checks': 3,
                          'observed_file_modes': modes}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, required=True)
    parser.add_argument('--native-dir', type=Path, required=True)
    parser.add_argument('--named', type=Path, required=True)
    args = parser.parse_args()
    LoggingIntegration(args.bin_dir, args.native_dir, args.named).run()
