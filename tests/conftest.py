"""Enable supplementary coverage only in an explicitly instrumented QA run."""
import os
from pathlib import Path
import sys


def pytest_configure(config):
    output = os.environ.get('BIND9_CONDITION_COVERAGE')
    if output:
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root / 'tools/quality'))
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
    ansible = getattr(session.config, '_bind9_ansible', None)
    if ansible is not None:
        ansible.close()
        ansible.save(os.environ['BIND9_CONDITION_COVERAGE'])
    templates = getattr(session.config, '_bind9_templates', None)
    if templates is not None:
        templates.close()
        templates.save(os.environ['BIND9_CONDITION_COVERAGE'])
    collector = getattr(session.config, '_bind9_collector', None)
    if collector is not None:
        collector.close()
        collector.save()
