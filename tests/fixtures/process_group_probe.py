"""Observe real descendant exit in a dedicated Linux child-subreaper process.

killpg sends an asynchronous signal, and waiting for its leader does not reap
grandchildren. This isolated probe adopts its own orphan, then checks the actual
wait status instead of racing a single /proc state sample. The parent-only mode
is a negative control: its live descendant must be refused and then cleaned up.
"""
import argparse
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time


class ProcessGroupProbe:
    """Own and reap only this probe's known descendant, with bounded waits."""

    WAIT_SECONDS = 5
    STILL_ALIVE = 23

    def __init__(self, module_path, parent_only=False):
        spec = importlib.util.spec_from_file_location('bind9_process_group_probe', module_path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.parent_only = parent_only
        self.child = None
        self.reaped = False

    @staticmethod
    def adopt_orphans():
        # PR_SET_CHILD_SUBREAPER is Linux ABI 36 (since 3.4); changing it in
        # this helper cannot change pytest's child ownership or signal state.
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
        libc.prctl.restype = ctypes.c_int
        if libc.prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), 'Unable to establish isolated child ownership')

    def wait_status(self):
        deadline = time.monotonic() + self.WAIT_SECONDS
        while True:
            pid, status = os.waitpid(self.child, os.WNOHANG)
            if pid:
                self.reaped = True
                return status
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.005)

    def cleanup(self):
        if self.child is None or self.reaped:
            return
        # waitpid establishes present child ownership before sending a signal;
        # a reused or unrelated PID must never be a cleanup target.
        pid, _ = os.waitpid(self.child, os.WNOHANG)
        if pid:
            self.reaped = True
            return
        os.kill(self.child, signal.SIGKILL)
        if self.wait_status() is None:
            raise AssertionError('Owned descendant did not terminate during bounded cleanup')

    def run(self):
        self.adopt_orphans()
        self.module.NativeCommand.TIMEOUT = 0.4
        if self.parent_only:
            # Deliberately defective dependency used only by the negative probe.
            self.module.os.killpg = lambda leader, sig: os.kill(leader, sig)
        with tempfile.TemporaryDirectory(prefix='bind9-descendant-probe-') as folder:
            marker = Path(folder) / 'descendant.pid'
            source = ('import subprocess,sys,time; from pathlib import Path; '
                      'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
                      'Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)')
            try:
                try:
                    self.module.Manager.command([sys.executable, '-c', source, str(marker)])
                except self.module.ManagementError as error:
                    if 'timed out' not in str(error):
                        raise AssertionError('Probe did not exercise the timeout path') from error
                else:
                    raise AssertionError('Probe command unexpectedly succeeded')
                self.child = int(marker.read_text())
                if self.child <= 1:
                    raise AssertionError('Probe did not establish a valid descendant')
                status = self.wait_status()
                if status is None:
                    print(json.dumps({'descendant_still_alive': True}))
                    return self.STILL_ALIVE
                if not os.WIFSIGNALED(status) or os.WTERMSIG(status) != signal.SIGKILL:
                    raise AssertionError('Descendant did not terminate through the owned group kill')
                print(json.dumps({'descendant_signal': int(signal.SIGKILL), 'reaped': True}))
                return 0
            finally:
                self.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('module_path')
    parser.add_argument('--parent-only', action='store_true')
    options = parser.parse_args()
    sys.exit(ProcessGroupProbe(options.module_path, options.parent_only).run())
