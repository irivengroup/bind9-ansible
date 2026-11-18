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
    @pytest.mark.parametrize('kind,expected,accepted', [
        ('valid', None, True), ('valid', 12, True), ('valid', 13, False),
        ('owner', None, False), ('rdata', None, False), ('overflow', None, False),
        ('additional', 12, False)])
    def test_unknown_serial_skips_only_equality_and_preserves_complete_soa_validation(self, kind, expected, accepted):
        row = 'EXAMPLE.TEST. 300 IN SOA ns.example. mail.example. 12 3600 900 1209600 300\n'
        answer = ';; status: NOERROR\n;; flags: qr aa; QUERY: 1, ANSWER: 1\n' + row
        if kind == 'owner': answer = answer.replace('EXAMPLE.TEST.', 'other.test.')
        elif kind == 'rdata': answer = answer.replace('ns.example. mail.example. 12 3600 900 1209600 300', 'incomplete')
        elif kind == 'overflow': answer = answer.replace(' 12 3600 ', ' 4294967296 3600 ')
        elif kind == 'additional':
            answer = answer.replace('ANSWER: 1', 'ANSWER: 2') + 'unrelated.test. 300 IN A 192.0.2.1\n'
        assert SoaHealth.matches(answer, 'example.test', expected) is accepted

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

    @pytest.mark.parametrize('serial', ['-1', '+1', '4294967296'])
    def test_serial_bounds_and_unsigned_decimal_grammar_are_preserved(self, serial):
        with pytest.raises(ValueError):
            SoaHealth.checked_serial('zone example.test/IN: loaded serial ' + serial, 'example.test')

    def test_blank_lines_are_ignored_but_duplicate_soa_answers_are_refused(self):
        header = ';; status: NOERROR\n;; flags: qr aa; QUERY: 1, ANSWER: 2\n\n'
        row = 'example.test. 300 IN SOA ns.example. mail.example. 12 3600 900 1209600 300\n'
        assert SoaHealth.response_complete(header + row + row)
        assert not SoaHealth.matches(header + row + row, 'example.test', 12)

    def test_package_install_precedes_primary_readiness_on_new_secondary(self):
        main = (ROOT / 'roles/bind9/tasks/tasks.d/deploy/main.yml').read_text()
        assert main.index('tasks.d/deploy/packages.yml') < main.index('tasks.d/deploy/primary_readiness.yml')

    @pytest.mark.parametrize('flags', ['qr aa-invalid', 'qr invalid-aa', 'qr x-aa-x'])
    def test_aa_must_be_a_whole_header_flag_token(self, flags):
        answer = (';; status: NOERROR\n;; flags: ' + flags + '; QUERY: 1, ANSWER: 1\n'
                  'example.test. 300 IN SOA ns.example. mail.example. 12 3600 900 1209600 300\n')
        assert not SoaHealth.matches(answer, 'example.test', 12)
