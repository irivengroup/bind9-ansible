"""Exercise canonical BIND token boundaries independently of Ansible rendering."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('transfer_acl', ROOT / 'roles/bind9/filter_plugins/transfer_acl.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
TransferAclGuard = MODULE.TransferAclGuard


class TestTransferAclGuard:
    """Keep nested negative ACLs valid while rejecting direct wildcard entries."""

    @pytest.mark.parametrize('output,expected', [
        ('allow-transfer { none; };', True),
        ('allow-transfer { ANY; };', False),
        ('allow-transfer { "Any"; };', False),
        ('allow-transfer { !any; };', True),
        ('allow-transfer { ! any; };', True),
        ('allow-transfer { key "any"; };', True),
        ('allow-transfer { !{ !peer; any; }; key "transfer"; };', True),
        ('allow-transfer { !{ !peer; any; }; any; };', False),
        ('allow-transfer { !{ !{ 192.0.2.2; }; any; }; "any"; };', False),
        ('allow-transfer { { 192.0.2.2; }; key "transfer"; };', True),
        ('allow-transfer { { 192.0.2.2; }; any; };', False),
        ('options { allow-transfer { none; }; }; zone "example.test" { allow-transfer { any; }; };', False),
        ('options { allow-transfer { key "{any;}"; }; };', True),
        ('options { allow-transfer { key "semi; brace} quoted\\\"name"; }; };', True),
        ('options { recursion yes; };', True),
        ('acl "allow-transfer" { any; };', True),
        ('allow-transfer { key "any" extra tokens; };', True),
        ('allow-transfer { } ; \n\t', True),
    ])
    def test_canonical_entry_scope(self, output, expected):
        assert TransferAclGuard.has_no_direct_wildcard(output) is expected

    @pytest.mark.parametrize('output', [
        None, False, 123, [], '', ' \n\t', 'options {\x00};',
        '"unterminated', 'options {} ; "', 'allow-transfer',
        'allow-transfer none;', 'allow-transfer { none }', 'allow-transfer { none;',
        'options {', '}', 'options { allow-transfer { none; }; }}',
        'allow-transfer { !{ peer; } };',
    ])
    def test_missing_or_incomplete_evidence_is_not_safe(self, output):
        assert TransferAclGuard.has_no_direct_wildcard(output) is False

    def test_input_and_each_nesting_branch_are_bounded(self, monkeypatch):
        monkeypatch.setattr(TransferAclGuard, 'MAX_OUTPUT', 64)
        assert TransferAclGuard.has_no_direct_wildcard(' ' * 65) is False
        monkeypatch.setattr(TransferAclGuard, 'MAX_DEPTH', 2)
        assert TransferAclGuard.has_no_direct_wildcard('options { allow-transfer { none; }; };') is True
        assert TransferAclGuard.has_no_direct_wildcard('options { acl "x" { allow-transfer { none; }; }; };') is False
        assert TransferAclGuard.has_no_direct_wildcard('options { allow-transfer { { peer; }; }; };') is False

    def test_long_acl_entry_state_is_bounded_without_losing_a_later_wildcard(self):
        prefix = 'allow-transfer { ' + ' '.join(['opaque'] * 10000) + '; '
        assert TransferAclGuard.has_no_direct_wildcard(prefix + 'none; };') is True
        assert TransferAclGuard.has_no_direct_wildcard(prefix + 'any; };') is False

    def test_full_entry_state_before_a_nested_group_preserves_the_outer_scope(self):
        """Descending after two classifications must not hide a later wildcard.

        This lexer guard leaves BIND grammar validation to named-checkconf;
        opaque tokens must neither grow its bounded state nor lose brace scope.
        """
        prefix = 'allow-transfer { first second { peer; }; '
        assert TransferAclGuard.has_no_direct_wildcard(prefix + 'none; };') is True
        assert TransferAclGuard.has_no_direct_wildcard(prefix + 'any; };') is False

    def test_ansible_exports_the_same_guard(self):
        exported = MODULE.FilterModule().filters()
        assert set(exported) == {'transfer_acl_has_no_direct_wildcard'}
        assert exported['transfer_acl_has_no_direct_wildcard']('allow-transfer { any; };') is False
