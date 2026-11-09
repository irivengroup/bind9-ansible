"""In-memory native fact vectors; separate profile-file integration checks.

Synthetic rows exercise the verifier's grammar, never claim script execution.
Actual interpreter/socket controls live in test_native_bash_observer.py.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/quality'))
from bash_protocol import (BashEventSchema, NativeHealthProfile, NativeFrontProfile,
                           NativeConfigtoolProfile, NativeLoginPathProfile, NativeCompleteRoleProfile)
from bind9_coverage import InstrumentationError

DIGEST, UNIT, PID = 'a' * 64, 'b' * 64, '123'


def event(family, kind, *payload):
    return [family, kind, PID, DIGEST] + list(payload)


@pytest.mark.parametrize('row', [
    event('B1', 'H'), event('B1', 'E', '0', '2'), event('B1', 'I', '3', '3', UNIT),
    event('B1', 'C', '3', '4', UNIT, '1'), event('B2', 'I', '4', '6', UNIT),
    event('B2', 'S', '4', '4', UNIT), event('B2', 'F', '4', '7', UNIT),
    event('B2', 'R', '4', '6', UNIT, '3', '0'), event('B3', 'I', '4', '106', UNIT),
    event('B3', 'C', '4', '103', UNIT, '0'), event('B3', 'R', '4', '102', UNIT, '1'),
    event('B4', 'I', '4', '201', UNIT), event('B4', 'A', '4', '201', UNIT),
    event('B4', 'C', '4', '202', UNIT, '1'), event('B5', 'M', 'sets'),
    event('B0', 'I', '3', '301', UNIT), event('B0', 'C', '3', '301', UNIT, '0'),
    event('B0', 'R', '5', '302', UNIT, '1'),
    event('B6', 'P', '4', '202', UNIT, '1'), event('B7', 'D', '4', '0', UNIT, '5'),
    event('B8', 'R', '4', '4', UNIT, '255'), event('B8', 'O', '4', '4', UNIT, '0'),
    event('B9', 'I', '4', '4', UNIT),
    event('B10', 'T', '4', '4', UNIT, '124', '1'),
    event('B10', 'W', '4', '4', UNIT, '124', '1', '127')])
def test_native_bash_closed_schema_accepts_inspected_typed_vectors_without_creating_hits(row):
    assert BashEventSchema.check(row, DIGEST) == tuple(row)


@pytest.mark.parametrize('row', [None, {}, [], ['B1', 'H', PID],
    event('B0', 'H'), event('B1', 'U'), event('B2', 'V', '4', '4', '0', '1'),
    event('B2', 'X', '1', '4', '4'), event('B1', 'E', '1', '0'),
    event('B1', 'E', '0', '256'), event('B5', 'M', 'raw'),
    event('B1', 'I', '0', '3', UNIT), event('B1', 'I', '4', '5', UNIT),
    event('B2', 'S', '4', '6', UNIT), event('B2', 'F', '4', '4', UNIT),
    event('B2', 'I', '4', '15', UNIT), event('B3', 'C', '4', '101', UNIT, '1'),
    event('B3', 'R', '4', '103', UNIT, '1'), event('B3', 'I', '4', '107', UNIT),
    event('B4', 'A', '4', '202', UNIT), event('B4', 'C', '4', '201', UNIT, '1'),
    event('B0', 'I', '3', '303', UNIT), event('B0', 'C', '3', '301', UNIT, '2'),
    event('B0', 'R', '5', '302', UNIT, '01'), event('B0', 'R', '5', '202', UNIT, '1'),
    event('B6', 'P', '4', '202', UNIT, '2'), event('B7', 'D', '4', '3', UNIT, '5'),
    event('B2', 'R', '4', '3', UNIT, '0', '2'), event('B8', 'R', '4', '4', UNIT, '-1'),
    event('B8', 'R', '4', '4', UNIT, '01'), event('B9', 'I', '4', '4', UNIT + '0'),
    event('B9', 'I', '4', '4', 'B' * 64), event('B1', 'H') + ['unexpected'],
    ['B1', 'H', True, DIGEST], ['B1', 'H', '0', DIGEST], ['B1', 'H', '001', DIGEST],
    ['B1', 'H', '-1', DIGEST], ['B1', 'H', '2147483648', DIGEST], ['B1', 'H', PID, 'c' * 64],
    event('B2', 'S', '4\n', '4', UNIT), event('B2', 'S', '4', '4', UNIT + '\x00'),
    event('B2', 'S', '4', '4', 'x' * 1024),
    event('B10', 'U', '1'), event('B10', 'F', '4', '4', UNIT, '124', '1'),
    event('B10', 'T', '4', '7', UNIT, '124', '1'),
    event('B10', 'T', '4', '4', UNIT, PID, '1'),
    event('B10', 'T', '4', '4', UNIT, '0', '1'),
    event('B10', 'T', '4', '4', UNIT, '01', '1'),
    event('B10', 'T', '4', '4', UNIT, '124', '0'),
    event('B10', 'T', '4', '4', UNIT, '124', '-1'),
    event('B10', 'T', '4', '4', UNIT, '124', '01'),
    event('B10', 'T', '4', '4', UNIT, '124', '2147483648'),
    event('B10', 'T', '4', '4', UNIT, '124', '1', 'extra'),
    event('B10', 'W', '4', '4', UNIT, PID, '1', '0'),
    event('B10', 'W', '4', '4', UNIT, '124', '1'),
    event('B10', 'W', '4', '4', UNIT, '124', '1', '-1'),
    event('B10', 'W', '4', '4', UNIT, '124', '1', '256'),
    event('B10', 'W', '4', '4', UNIT, '124', '1', 'unknown'),
    event('B10', 'W', '4', '4', UNIT, '124', '1', '01')])
def test_native_bash_closed_schema_refuses_malformed_foreign_and_unsupported_vectors(row):
    with pytest.raises(InstrumentationError):
        BashEventSchema.check(row, DIGEST)


def static_rows(profile):
    """Synthetic protocol fixture, not an execution or a measured artifact."""
    rows = [['B1', 'H', PID, profile.SOURCE_SHA256], ['B5', 'M', PID, profile.SOURCE_SHA256, profile.EVENT_MODE]]
    rows += [list(value[:2]) + [PID, profile.SOURCE_SHA256] + list(value[2:])
             for value in sorted(profile.declarations)]
    rows += [['B1', 'E', PID, profile.SOURCE_SHA256, '0', '0']]
    return rows


def terminal_rows(profile, status=1):
    """Synthetic pair grammar only: these rows never claim Bash execution."""
    rows = static_rows(profile)
    unit = next(value for value in sorted(profile.statuses) if value[1] == '4')
    source = profile.SOURCE_SHA256
    rows[-1:-1] = [
        ['B1', 'H', '124', source], ['B5', 'M', '124', source, profile.EVENT_MODE],
        ['B10', 'T', '124', source] + list(unit) + [PID, '1'],
        ['B10', 'W', PID, source] + list(unit) + ['124', '1', str(status)],
    ]
    return rows, unit


@pytest.mark.parametrize('profile_type', [NativeFrontProfile, NativeConfigtoolProfile])
@pytest.mark.parametrize('status', [0, 1, 126, 127, 130, 143, 255])
def test_terminal_native_wait_covers_only_its_declared_condition(profile_type, status):
    profile = profile_type(ROOT)
    rows, unit = terminal_rows(profile, status)
    # The program itself returned zero, including when its child predicate
    # failed. A wait fact must never borrow that enclosing program's result.
    hits = profile.check_process(rows, int(PID), 0)
    assert hits['conditions'] == {('status',) + unit + (status == 0,)}
    assert all(not hits[name] for name in profile.METRICS if name != 'conditions')


@pytest.mark.parametrize('fault', [
    'duplicate-intent', 'duplicate-wait', 'missing-intent', 'missing-wait',
    'wait-before-intent', 'unknown-parent', 'different-observed-parent',
    'wrong-child', 'wrong-serial', 'wrong-wait-unit', 'unknown-intent-unit',
    'parent-exited', 'missing-child-header', 'wrong-child-mode',
    'wait-after-main-exit', 'main-intent', 'reused-retired-serial',
    'reused-serial-other-child', 'nonpredicate-unit', 'syntax-only',
])
def test_terminal_process_receipt_refuses_forged_incomplete_or_replayed_pairs(fault):
    profile = NativeFrontProfile(ROOT)
    rows, unit = terminal_rows(profile)
    intent = next(i for i, row in enumerate(rows) if row[:2] == ['B10', 'T'])
    wait = next(i for i, row in enumerate(rows) if row[:2] == ['B10', 'W'])
    source = profile.SOURCE_SHA256
    if fault == 'duplicate-intent': rows.insert(intent+1, rows[intent].copy())
    elif fault == 'duplicate-wait': rows.insert(wait+1, rows[wait].copy())
    elif fault == 'missing-intent': rows.pop(intent)
    elif fault == 'missing-wait': rows.pop(wait)
    elif fault == 'wait-before-intent': rows.insert(intent, rows.pop(wait))
    elif fault == 'unknown-parent': rows[intent][7] = '125'
    elif fault == 'different-observed-parent':
        rows[intent][7] = '125'
        rows[intent:intent] = [['B1', 'H', '125', source],
                              ['B5', 'M', '125', source, profile.EVENT_MODE]]
    elif fault == 'wrong-child': rows[wait][7] = '125'
    elif fault == 'wrong-serial': rows[wait][8] = '2'
    elif fault == 'wrong-wait-unit': rows[wait][6] = UNIT
    elif fault == 'unknown-intent-unit': rows[intent][6] = UNIT
    elif fault == 'parent-exited':
        rows.insert(intent, ['B1', 'E', PID, source, '0', '0'])
    elif fault == 'missing-child-header': rows.pop(intent-2)
    elif fault == 'wrong-child-mode': rows[intent-1][4] = 'entry_sets'
    elif fault == 'wait-after-main-exit': rows.append(rows.pop(wait))
    elif fault == 'main-intent':
        rows[intent][2], rows[intent][7] = PID, '124'
        rows[wait][2], rows[wait][7] = '124', PID
    elif fault == 'reused-retired-serial':
        rows[wait+1:wait+1] = [rows[intent].copy(), rows[wait].copy()]
    elif fault == 'reused-serial-other-child':
        duplicate = rows[intent].copy()
        duplicate[2] = '125'
        rows[intent+1:intent+1] = [['B1', 'H', '125', source],
            ['B5', 'M', '125', source, profile.EVENT_MODE], duplicate]
    elif fault == 'nonpredicate-unit':
        other = next(value for value in sorted(profile.commands-profile.statuses) if value[1] == '4')
        rows[intent][4:7] = rows[wait][4:7] = list(other)
    with pytest.raises(InstrumentationError):
        profile.check_process(rows, int(PID), 0, syntax=fault == 'syntax-only')


def test_native_health_profile_file_keeps_every_reviewed_unit_and_syntax_never_covers_execution():
    profile = NativeHealthProfile(ROOT)
    assert {name: len(units) for name, units in profile.units.items()} == {
        'statements': 71, 'branches': 46, 'functions': 3, 'lines': 39, 'conditions': 44}
    assert all(not values for values in profile.check_process(static_rows(profile), 123, 0, syntax=True).values())


def test_native_path_profile_retains_parameter_choices_case_alternatives_and_no_inferred_hits():
    profile = NativeLoginPathProfile(ROOT)
    assert {name: len(units) for name, units in profile.units.items()} == {
        'statements': 3, 'branches': 6, 'functions': 0, 'lines': 2, 'conditions': 6}
    assert len(profile.parameters) == 2 and len(profile.arms) == 2
    assert len(profile.patterns) == 2 and len(profile.unconditional) == 1
    assert all(not values for values in profile.check_process(static_rows(profile), 123, 0, syntax=True).values())
    rows = static_rows(profile)
    unit = sorted(profile.parameters)[0]
    rows.insert(-1, ['B0', 'C', PID, profile.SOURCE_SHA256] + list(unit) + ['0'])
    hits = profile.check_process(rows, 123, 0)
    assert hits['conditions'] == {('parameter',) + unit + (False,)}
    assert all(not hits[name] for name in profile.METRICS if name != 'conditions')


@pytest.mark.parametrize('fault', ['parameter', 'pattern', 'arm', 'false-default', 'missing-pattern-metadata'])
def test_native_path_profile_refuses_foreign_units_impossible_defaults_and_truncated_metadata(fault):
    profile = NativeLoginPathProfile(ROOT)
    rows = static_rows(profile)
    if fault == 'missing-pattern-metadata':
        rows = [row for row in rows if row[:2] != ['B6', 'P']]
    else:
        if fault == 'parameter':
            row = ['B0', 'C', PID, profile.SOURCE_SHA256, '3', '301', UNIT, '1']
        elif fault == 'pattern':
            row = ['B4', 'C', PID, profile.SOURCE_SHA256, '3', '202', UNIT, '1']
        elif fault == 'arm':
            row = ['B4', 'A', PID, profile.SOURCE_SHA256, '3', '201', UNIT]
        else:
            row = ['B4', 'C', PID, profile.SOURCE_SHA256] + list(next(iter(profile.unconditional))) + ['0']
        rows.insert(-1, row)
    with pytest.raises(InstrumentationError):
        profile.check_process(rows, 123, 0, syntax=fault == 'missing-pattern-metadata')


@pytest.mark.parametrize('fault', ['missing-header', 'duplicate-header', 'missing-mode', 'duplicate-mode',
    'missing-exit', 'wrong-exit', 'after-exit', 'wrong-main', 'truncated-inventory', 'unknown-inventory',
    'runtime-in-syntax', 'foreign-entry', 'wrong-function', 'unknown-control', 'unknown-boolean',
    'unknown-status', 'unattributed', 'missing-subprocess-mode'])
def test_native_health_profile_file_rejects_incomplete_or_fabricated_fact_structures(fault):
    profile = NativeHealthProfile(ROOT)
    rows, syntax = static_rows(profile), True
    if fault == 'missing-header': del rows[0]
    elif fault == 'duplicate-header': rows.insert(1, rows[0].copy())
    elif fault == 'missing-mode': del rows[1]
    elif fault == 'duplicate-mode': rows.insert(2, rows[1].copy())
    elif fault == 'missing-exit': rows.pop()
    elif fault == 'wrong-exit': rows[-1][-1] = '2'
    elif fault == 'after-exit': rows.append(rows[2].copy())
    elif fault == 'wrong-main': rows[0][2] = '124'
    elif fault == 'truncated-inventory': del rows[2]
    elif fault == 'unknown-inventory': rows[2][-1] = 'f' * 64
    elif fault == 'missing-subprocess-mode': rows.insert(-1, ['B1', 'H', '124', profile.SOURCE_SHA256])
    else:
        unit = sorted(profile.statement_units)[0]
        observation = ['B2', 'S', PID, profile.SOURCE_SHA256] + list(unit)
        if fault == 'foreign-entry': observation[-1] = 'f' * 64
        elif fault == 'wrong-function':
            # The lexically first statement can itself be a real function
            # definition. Use an actual simple-command origin for this refusal.
            simple = next(value for value in sorted(profile.statement_units) if value[1] == '4')
            observation = ['B2', 'F', PID, profile.SOURCE_SHA256] + list(simple)
        elif fault == 'unknown-control': observation = ['B2', 'R', PID, profile.SOURCE_SHA256, '4', '3', UNIT, '0', '1']
        elif fault == 'unknown-boolean': observation = ['B3', 'C', PID, profile.SOURCE_SHA256, '4', '103', UNIT, '1']
        elif fault == 'unknown-status': observation = ['B8', 'R', PID, profile.SOURCE_SHA256, '4', '4', UNIT, '0']
        elif fault == 'unattributed': observation = ['B2', 'U', PID, profile.SOURCE_SHA256, '4', '4']
        rows.insert(-1, observation)
        syntax = fault == 'runtime-in-syntax'
    with pytest.raises(InstrumentationError):
        profile.check_process(rows, 123, 0, syntax=syntax)


def test_native_health_profile_file_does_not_infer_unobserved_outcomes_from_an_entry():
    profile = NativeHealthProfile(ROOT)
    rows = static_rows(profile)
    unit = sorted(profile.statement_units)[0]
    rows.insert(-1, ['B2', 'S', PID, profile.SOURCE_SHA256] + list(unit))
    rows.insert(-1, copy.deepcopy(rows[-2]))
    hits = profile.check_process(rows, 123, 0)
    assert hits['statements'] == {unit} and hits['lines'] == {int(unit[0])}
    assert not hits['branches'] and not hits['functions'] and not hits['conditions']


# Keep the original sealed campaign's case identifiers; expectations include
# the complete production functions measured independently for each source.
@pytest.mark.parametrize('profile_type,count', [
    pytest.param(NativeFrontProfile, 39, id='NativeFrontProfile-37'),
    pytest.param(NativeConfigtoolProfile, 16, id='NativeConfigtoolProfile-14')])
def test_complete_profiles_keep_all_five_independent_denominators(profile_type, count):
    profile = profile_type(ROOT)
    assert len(profile.units['functions']) == count
    assert profile.units['statements'] and profile.units['lines']
    assert profile.units['branches'] and profile.units['conditions']
    rows = static_rows(profile)
    assert all(not hits for hits in profile.check_process(rows, 123, 0, syntax=True).values())
    unit = sorted(profile.function_units)[0]
    rows.insert(-1, ['B2', 'F', PID, profile.SOURCE_SHA256] + list(unit))
    hits = profile.check_process(rows, 123, 0)
    assert hits['functions'] == {unit}
    assert all(not hits[name] for name in profile.METRICS if name != 'functions')


@pytest.mark.parametrize('profile_type', [NativeHealthProfile, NativeFrontProfile, NativeConfigtoolProfile, NativeLoginPathProfile])
def test_each_native_profile_refuses_a_different_observation_mode(profile_type):
    profile = profile_type(ROOT)
    rows = static_rows(profile)
    rows[1][-1] = 'function_sets' if profile.EVENT_MODE == 'sets' else 'sets'
    with pytest.raises(InstrumentationError, match='mode differs'):
        profile.check_process(rows, 123, 0, syntax=True)


@pytest.mark.parametrize('family,event,payload', [('B3', 'C', ['1']), ('B8', 'R', ['0']), ('B2', 'R', ['0', '1'])])
def test_entry_facts_cannot_invent_boolean_or_control_outcomes(family, event, payload):
    profile = NativeFrontProfile(ROOT)
    rows = static_rows(profile)
    unit = sorted(profile.function_units)[0]
    # A function-entry identity cannot impersonate a condition or control node.
    if family == 'B3':
        unit = (unit[0], '103', unit[2])
    elif family == 'B2':
        unit = (unit[0], '3', unit[2])
    rows.insert(-1, [family, event, PID, profile.SOURCE_SHA256] + list(unit) + payload)
    if family == 'B8':
        # A native status is valid evidence, but a function definition is not a
        # declared command predicate and cannot grant Boolean execution credit.
        hits = profile.check_process(rows, 123, 0)
        assert not hits['branches'] and not hits['conditions']
    else:
        with pytest.raises(InstrumentationError):
            profile.check_process(rows, 123, 0)


@pytest.mark.parametrize('profile_type', [NativeFrontProfile, NativeConfigtoolProfile])
def test_native_entry_hit_does_not_infer_a_function_call_or_boolean_outcome(profile_type):
    profile = profile_type(ROOT)
    rows = static_rows(profile)
    unit = sorted(profile.function_units)[0]
    rows.insert(-1, ['B2', 'S', PID, profile.SOURCE_SHA256] + list(unit))
    hits = profile.check_process(rows, 123, 0)
    assert hits['statements'] == {unit}
    assert hits['lines'] == {int(unit[0])}
    assert not hits['functions'] and not hits['branches'] and not hits['conditions']


@pytest.mark.parametrize('family', ['W1', 'W2'])
@pytest.mark.parametrize('field,value', [(4, 'invalid'), (5, 'bad'), (6, 'none'), (7, '0'), (7, '17')])
def test_native_word_metadata_rejects_invalid_identities_and_lexical_depth(family, field, value):
    row = [family, 'I', PID, NativeFrontProfile.SOURCE_SHA256, UNIT, UNIT, UNIT, '1']
    row[field] = value
    with pytest.raises(InstrumentationError):
        BashEventSchema.check(row, NativeFrontProfile.SOURCE_SHA256)


@pytest.mark.parametrize('profile_type', [NativeHealthProfile, NativeFrontProfile])
def test_each_profile_retains_its_own_bounded_inventory_limit(tmp_path, monkeypatch, profile_type):
    path = ROOT / 'tools/quality/native_bash' / profile_type.INVENTORY_NAME
    content = path.read_bytes()
    altered = tmp_path / 'oversized-profile.json'
    altered.write_bytes(content + b' ' * (profile_type.MAX_INVENTORY_BYTES + 1 - len(content)))
    monkeypatch.setattr(profile_type, 'INVENTORY_NAME', str(altered))
    monkeypatch.setattr(profile_type, 'INVENTORY_SHA256', hashlib.sha256(altered.read_bytes()).hexdigest())
    with pytest.raises(InstrumentationError, match='inventory changed'):
        profile_type(ROOT)


@pytest.mark.parametrize('fault', ['missing-census', 'missing-word', 'unknown-subtree',
                                 'duplicate-census', 'conflicting-word', 'conflicting-shape',
                                 'ambiguous-owner', 'unsupported-family'])
def test_entry_inventory_requires_the_whole_unambiguous_native_word_census(tmp_path, monkeypatch, fault):
    profile_type = NativeFrontProfile
    data = json.loads((ROOT / 'tools/quality/native_bash' / profile_type.INVENTORY_NAME).read_text())
    declarations = data['declarations']
    words = [row for row in declarations if row[:2] == ['W1', 'I']]
    census = [row for row in declarations if row[:2] == ['W2', 'I']]
    if fault == 'missing-census':
        declarations.remove(census[0])
    elif fault == 'missing-word':
        declarations.remove(words[0])
    elif fault == 'unknown-subtree':
        site_id = census[0][2]
        for row in declarations:
            if row[:2] in (['W1', 'I'], ['W2', 'I']) and row[2] == site_id:
                row[2] = '0' * 64
    elif fault in ('duplicate-census', 'conflicting-word', 'conflicting-shape'):
        row = copy.deepcopy(census[0] if fault == 'duplicate-census' else words[0])
        row[4 if fault == 'conflicting-shape' else 3] = '0' * 64
        declarations.append(row)
    elif fault == 'ambiguous-owner':
        source_id = census[1][2]
        for row in declarations:
            if row[:2] in (['W1', 'I'], ['W2', 'I']) and row[2] == source_id:
                row[3:5] = census[0][3:5]
    else:
        declarations.append(['B1', 'I', '1', '3', UNIT])
    altered = tmp_path / 'changed-profile.json'
    altered.write_text(json.dumps(data))
    monkeypatch.setattr(profile_type, 'INVENTORY_NAME', str(altered))
    monkeypatch.setattr(profile_type, 'INVENTORY_SHA256', hashlib.sha256(altered.read_bytes()).hexdigest())
    with pytest.raises(InstrumentationError):
        profile_type(ROOT)


@pytest.fixture(scope='module', params=[NativeFrontProfile, NativeConfigtoolProfile])
def complete_profile(request):
    return request.param(ROOT)


@pytest.mark.parametrize('family,kind', sorted(NativeCompleteRoleProfile.STATIC_EVENTS))
def test_complete_native_syntax_cannot_omit_any_declared_kind(complete_profile, family, kind):
    profile = complete_profile
    original = static_rows(profile)
    rows = [row for row in original if row[:2] != [family, kind]]
    assert len(rows) < len(original)
    with pytest.raises(InstrumentationError):
        profile.check_process(rows, 123, 0, syntax=True)


@pytest.mark.parametrize('family', ['A3', 'P3'])
@pytest.mark.parametrize('field', [4, 6, 7, 8, 9, 10])
def test_native_reparse_retains_every_stable_source_field(complete_profile, family, field):
    profile = complete_profile
    rows = static_rows(profile)
    row = next(row for row in rows if row[:2] == [family, 'L'])
    row[field] = '0' * 64 if field in (9, 10) else str(int(row[field]) + 1)
    with pytest.raises(InstrumentationError):
        profile.check_process(rows, 123, 0, syntax=True)


@pytest.mark.parametrize('family', ['A3', 'P3'])
def test_native_parser_allocation_serial_is_not_execution_credit(complete_profile, family):
    profile = complete_profile
    rows = static_rows(profile)
    for row in rows:
        if row[:2] == [family, 'L']:
            row[5] = str(int(row[5]) + 10000)
    hits = profile.check_process(rows, 123, 0, syntax=True)
    assert not any(hits.values())


@pytest.mark.parametrize('truth', [False, True])
@pytest.mark.parametrize('legacy', [False, True])
def test_native_arithmetic_root_does_not_duplicate_elementary_outcomes(complete_profile, truth, legacy):
    # These typed vectors exercise the verifier only. Real source execution and
    # kernel PID credentials are qualified by the native collector tests.
    profile = complete_profile
    model = profile.arithmetic
    owner = next(owner for owner in sorted(model.predicates) if model.predicates[owner])
    index = min(model.predicates[owner])
    line, bit = str(model.lines[owner]), str(int(truth))
    value = ['A2', 'V', PID, profile.SOURCE_SHA256, owner, line, str(index)]
    value += [str(part) for part in model.graphs[owner][index]] + ['1', bit]
    root = ['A2', 'G', PID, profile.SOURCE_SHA256, owner, line, str(model.roots[owner]), '1', bit]
    rows = static_rows(profile)
    rows[-1:-1] = [value, root]
    hits = profile.check_process(rows, 123, 0)
    assert hits['conditions'] == {('arithmetic', owner, index, truth)}
    assert not hits['statements'] and not hits['functions'] and not hits['lines']
    if legacy:
        rows.insert(-1, ['A1', 'R', PID, profile.SOURCE_SHA256, owner, line, '1', bit])
        assert profile.check_process(rows, 123, 0) == hits
    rows.remove(root)
    with pytest.raises(InstrumentationError, match='Uncompleted'):
        profile.check_process(rows, 123, 0)


@pytest.mark.parametrize('truth', [False, True])
def test_native_parameter_choice_needs_both_same_pid_facts(complete_profile, truth):
    profile = complete_profile
    site, metadata = next(iter(sorted(profile.parameters_model.selectors.items())))
    facts = [[family, event, PID, profile.SOURCE_SHA256, site] + list(metadata) + [str(int(truth))]
             for family, event in [('P4', 'C'), ('P4', 'R')]]
    rows = static_rows(profile)
    rows[-1:-1] = facts
    hits = profile.check_process(rows, 123, 0)
    assert hits['branches'] == hits['conditions'] == {('selector', site, truth)}
    assert not hits['statements'] and not hits['functions'] and not hits['lines']
    foreign = copy.deepcopy(facts)
    foreign[1][2] = '124'
    with pytest.raises(InstrumentationError, match='Unpaired'):
        profile.parameters_model.observe(foreign)
    rows.remove(facts[0])
    with pytest.raises(InstrumentationError, match='Unpaired'):
        profile.check_process(rows, 123, 0)


@pytest.mark.parametrize('fault', ['missing-authored', 'unparsed', 'missing-node', 'foreign-child', 'unsupported-kind'])
def test_native_arithmetic_census_and_tree_are_complete(complete_profile, fault):
    from bash_expressions import ArithmeticBooleanModel
    profile = complete_profile
    rows = static_rows(profile)
    if fault == 'missing-authored':
        rows = [row for row in rows if row[:2] != ['A1', 'I']]
    elif fault == 'unparsed':
        next(row for row in rows if row[:2] == ['A1', 'P'])[6] = '0'
    else:
        row = next(row for row in rows if row[:2] == ['A2', 'I'])
        if fault == 'missing-node':
            rows.remove(row)
        elif fault == 'foreign-child':
            row[8] = row[6]
        else:
            row[7] = '1150'
    with pytest.raises(InstrumentationError):
        ArithmeticBooleanModel(profile.SOURCE_SHA256, rows)


@pytest.mark.parametrize('row', [
    event('A2', 'U', UNIT, '1', '0', '0'),
    event('P4', 'U', '45', '0', '0'),
    event('A2', 'G', UNIT, '1', '1', '0', '1'),
    event('A2', 'V', UNIT, '1', '1', '5', '0', '0', '0', '1', '2'),
    event('P4', 'C', UNIT, '1', '35', '0', '1'),
    event('P3', 'L', '1', '1', '1', '1', '1', UNIT),
])
def test_native_expression_schema_refuses_unknown_unattributed_or_invalid_results(row):
    with pytest.raises(InstrumentationError):
        BashEventSchema.check(row, DIGEST)
