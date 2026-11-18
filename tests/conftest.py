"""Enable supplementary coverage only in an explicitly instrumented QA run."""
import hashlib
import os
from pathlib import Path
import sys
from types import ModuleType

import pytest


@pytest.fixture
def legacy_ipaddress():
    """Load the exact supported EL8 parser as an isolated QA dependency.

    Hash and execute the same bytes. A changed or missing fixture is an error,
    never a fallback to the host parser, whose mapped-address rules differ.
    """
    source = Path(__file__).parent / 'fixtures/legacy_ipaddress_el8.py'
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != '97d6fbeef7261d5bdc336228d4482749bdec1761360e7684ff5c50d31406a91a':
        pytest.fail('The pinned EL8 standard-library parser fixture changed')
    module = ModuleType('bind9_legacy_ipaddress_el8')
    module.__file__ = str(source)
    exec(compile(raw, str(source), 'exec'), module.__dict__)
    return module


def pytest_configure(config):
    output = os.environ.get('BIND9_CONDITION_COVERAGE')
    bash_output = os.environ.get('BIND9_NATIVE_BASH_OUTPUT')
    if output or bash_output:
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root / 'tools/quality'))
    if bash_output:
        from bash_coverage import NativeBashCollector
        manifest = os.environ.get('BIND9_NATIVE_BASH_MANIFEST')
        if not manifest:
            raise RuntimeError('Native Bash observations require their explicit paired build manifest')
        config._bind9_bash = NativeBashCollector(root, manifest, os.environ.get('BIND9_NATIVE_BASH_MODE'))
        config._bind9_bash.install()
    if output:
        from bind9_coverage import PythonCollector
        config._bind9_collector = PythonCollector(root, output)
        config._bind9_collector.install()
        from jinja_coverage import JinjaCollector
        config._bind9_templates = JinjaCollector(root)
        config._bind9_templates.install()
        from ansible_coverage import AnsibleCollector
        config._bind9_ansible = AnsibleCollector(root)
        config._bind9_ansible.install()


def pytest_sessionfinish(session, exitstatus):
    try:
        ansible = getattr(session.config, '_bind9_ansible', None)
        if ansible is not None:
            ansible.close()
            ansible.save(os.environ['BIND9_CONDITION_COVERAGE'])
            ansible.merge(os.environ['BIND9_CONDITION_COVERAGE'])
        templates = getattr(session.config, '_bind9_templates', None)
        if templates is not None:
            templates.close()
            templates.save(os.environ['BIND9_CONDITION_COVERAGE'])
        collector = getattr(session.config, '_bind9_collector', None)
        if collector is not None:
            collector.close()
            collector.save()
    finally:
        try:
            bash = getattr(session.config, '_bind9_bash', None)
            if bash is not None:
                try:
                    bash.close()
                except Exception:
                    bash.error('Native Bash observer did not shut down cleanly')
                    raise
                finally:
                    bash.save(os.environ['BIND9_NATIVE_BASH_OUTPUT'])
        finally:
            # Each native boundary has one cleanup owner, even after a refusal.
            boundary = sys.modules.get('canonical_shell_lab')
            if boundary is not None:
                boundary.CanonicalFrontEnvironment.release()
