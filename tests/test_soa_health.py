"""Regression for reverse serial reconciliation and exact SOA comparisons."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('soa_health', ROOT / 'roles/bind9/filter_plugins/soa_health.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SoaHealth = MODULE.SoaHealth


class TestSoaHealth:
    def test_dns_owner_comparison_is_case_insensitive(self):
        assert SoaHealth.checked_serial('zone example.test/IN: loaded serial 12', 'EXAMPLE.TEST') == 12
        answer = ';; status: NOERROR\n;; flags: qr aa; QUERY: 1, ANSWER: 1\nexample.test. 300 IN SOA ns.example. mail.example. 12 3600 900 1209600 300\n'
        assert SoaHealth.matches(answer, 'EXAMPLE.TEST', 12)

    def test_actual_advanced_reverse_serial_is_used(self):
        output = 'zone 113.0.203.in-addr.arpa/IN: loaded serial 2026101402\nOK\n'
        assert SoaHealth.checked_serial(output, '113.0.203.in-addr.arpa') == 2026101402
        assert MODULE.FilterModule().filters()['checked_soa_serial'](output, '113.0.203.in-addr.arpa') == 2026101402
        answer = ';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n;; flags: qr aa; QUERY: 1, ANSWER: 1\n113.0.203.in-addr.arpa. 300 IN SOA ns.example. hostmaster.example. 2026101402 3600 900 1209600 300\n'
        assert SoaHealth.matches(answer, '113.0.203.in-addr.arpa', 2026101402)
        assert not SoaHealth.matches(answer, '113.0.203.in-addr.arpa', 2026101401)

    def test_old_substring_match_cannot_accept_serial_from_a_timer(self):
        answer = ';; status: NOERROR\n;; flags: qr aa; QUERY: 1, ANSWER: 1\nexample.test. 300 IN SOA ns.example. mail.example. 12 3600 900 1209600 300\n'
        assert '3600' in answer  # Former condition accepted any matching substring.
        assert not SoaHealth.matches(answer, 'example.test', 3600)
        assert SoaHealth.matches(answer, 'example.test', 12)
        assert not SoaHealth.matches(answer.replace('qr aa', 'qr ra'), 'example.test', 12)
        assert not SoaHealth.matches(answer.replace('NOERROR', 'SERVFAIL'), 'example.test', 12)

    @pytest.mark.parametrize('output', ['', 'zone other.test/IN: loaded serial 12',
                                        'zone example.test/IN: loaded serial 4294967296'])
    def test_invalid_checkzone_output_is_explicit_failure(self, output):
        with pytest.raises(ValueError):
            SoaHealth.checked_serial(output, 'example.test')

    def test_package_install_precedes_primary_readiness_on_new_secondary(self):
        main = (ROOT / 'roles/bind9/tasks/tasks.d/deploy/main.yml').read_text()
        assert main.index('tasks.d/deploy/packages.yml') < main.index('tasks.d/deploy/primary_readiness.yml')
