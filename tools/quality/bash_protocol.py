"""Validate bounded native Bash facts without executing shell expressions.

Reviewed profiles declare exactly which independent metrics are measured.
Static declarations remain separate from execution, including code after an
early process exit. An unknown metric never becomes an empty denominator.
"""
import hashlib
import json
from pathlib import Path
import re

from bind9_coverage import InstrumentationError
from bash_expressions import NativeExpressionSchema, ArithmeticBooleanModel, ParameterBooleanModel


class BashEventSchema:
    """Closed datagram schema emitted by the inspected native interpreter."""

    SIZES = {
        ('B0', 'I'): 7, ('B0', 'C'): 8, ('B0', 'R'): 8,
        ('B1', 'H'): 4, ('B1', 'E'): 6, ('B1', 'I'): 7, ('B1', 'C'): 8,
        ('B2', 'I'): 7, ('B2', 'S'): 7, ('B2', 'F'): 7, ('B2', 'R'): 9,
        ('B3', 'I'): 7, ('B3', 'C'): 8, ('B3', 'R'): 8,
        ('B4', 'I'): 7, ('B4', 'C'): 8, ('B4', 'A'): 7,
        ('B5', 'M'): 5, ('B6', 'P'): 8, ('B7', 'D'): 8,
        ('B8', 'R'): 8, ('B8', 'O'): 8, ('B9', 'I'): 7,
        ('B10', 'T'): 9, ('B10', 'W'): 10,
        ('W1', 'I'): 8, ('W2', 'I'): 8}
    DIGEST = re.compile('[0-9a-f]{64}\\Z')
    NUMBER = re.compile('(?:0|[1-9][0-9]*)\\Z')
    CONTROL_KINDS = (3, 2, 8, 6, 6, 0)

    @classmethod
    def number(cls, value, maximum=2147483647, minimum=0):
        if (not isinstance(value, str) or len(value) > 10 or cls.NUMBER.fullmatch(value) is None or
                not minimum <= int(value) <= maximum):
            raise InstrumentationError('Invalid native Bash numeric field')
        return int(value)

    @classmethod
    def check(cls, row, source):
        if (not isinstance(row, (list, tuple)) or not 4 <= len(row) <= 13 or
                not all(isinstance(value, str) for value in row) or
                sum(len(value) + 1 for value in row) > 1024 or
                any('\x00' in value or '\n' in value or '\r' in value or '\t' in value for value in row)):
            raise InstrumentationError('Malformed native Bash event')
        family, event = row[:2]
        cls.number(row[2], minimum=1)
        if cls.DIGEST.fullmatch(source) is None or row[3] != source:
            raise InstrumentationError('Native Bash event belongs to a different source')
        if family in NativeExpressionSchema.FAMILIES:
            return NativeExpressionSchema.check(row, cls)
        if event in ('U', 'V', 'X'):
            raise InstrumentationError('Native Bash reported an unsupported or unattributed source unit')
        if cls.SIZES.get((family, event)) != len(row):
            raise InstrumentationError('Unknown native Bash event schema')
        if (family, event) == ('B1', 'H'):
            return tuple(row)
        if (family, event) == ('B1', 'E'):
            if cls.number(row[4], maximum=1) != 0:
                raise InstrumentationError('Native Bash overflow or source-integrity failure')
            cls.number(row[5], maximum=255)
            return tuple(row)
        if family == 'B5':
            if row[4] not in ('sets', 'function_sets', 'entry_sets', 'decision_sets'):
                raise InstrumentationError('Admitted Bash observations require exact event-set mode')
            return tuple(row)
        if family in ('W1', 'W2'):
            if any(cls.DIGEST.fullmatch(value) is None for value in row[4:7]):
                raise InstrumentationError('Invalid native Bash word provenance')
            cls.number(row[7], minimum=1, maximum=16)
            return tuple(row)
        cls.number(row[4], minimum=1)
        kind = cls.number(row[5], maximum=302)
        if cls.DIGEST.fullmatch(row[6]) is None:
            raise InstrumentationError('Invalid native Bash structural identity')
        if family == 'B10':
            if kind != 4 or row[2] == row[7]:
                raise InstrumentationError('Invalid native terminal actor or command kind')
            cls.number(row[7], minimum=1)
            cls.number(row[8], minimum=1)
            if event == 'W':
                cls.number(row[9], maximum=255)
            return tuple(row)
        if family == 'B0':
            if kind not in (301, 302):
                raise InstrumentationError('Unknown native parameter-choice kind')
        elif family == 'B1':
            if kind not in (3, 4):
                raise InstrumentationError('Unknown native legacy conditional kind')
        elif family in ('B2', 'B8', 'B9'):
            if not 0 <= kind <= 14 or (event == 'F' and kind != 7) or (event == 'S' and kind == 6):
                raise InstrumentationError('Unknown or misclassified native command kind')
        elif family == 'B3':
            allowed = {'I': (101, 102, 103, 104, 105, 106), 'C': (103, 104), 'R': (101, 102)}[event]
            if kind not in allowed:
                raise InstrumentationError('Unknown native conditional kind')
        elif family in ('B4', 'B6'):
            allowed = (201, 202) if event == 'I' else ((201,) if event == 'A' else (202,))
            if kind not in allowed:
                raise InstrumentationError('Unknown native case kind')
        if family == 'B7' or (family == 'B2' and event == 'R'):
            tag = cls.number(row[7], maximum=5)
            if kind != cls.CONTROL_KINDS[tag]:
                raise InstrumentationError('Native control kind differs from its branch tag')
        if family == 'B8':
            cls.number(row[7], maximum=255)
        elif event == 'C' or (family in ('B0', 'B3', 'B2') and event == 'R') or family == 'B6':
            cls.number(row[-1], maximum=1)
        return tuple(row)


class NativeTerminalPairs:
    """Retire a native child intent using that PID's actual parent wait fact.

    A child publishes its declared source origin immediately before exec.
    Only its matching live interpreter parent can supply the computed wait
    status. An intent alone is never a condition hit or a parent-result guess.
    """

    MAX_PENDING = 4096

    def __init__(self, predicates, main):
        self.predicates = predicates
        self.main = str(main)
        self.pending = {}
        self.serials = set()

    def observe(self, row, states):
        """Return (source unit, native status) only for a complete exact pair."""
        unit = row[4:7]
        if unit not in self.predicates:
            raise InstrumentationError('Native terminal status has no declared predicate')
        if row[1] == 'T':
            parent = states.get(row[7])
            if (row[2] == self.main or parent is None or not parent['mode'] or
                    parent['exit'] is not None or row[8] in self.serials or
                    len(self.pending) >= self.MAX_PENDING):
                raise InstrumentationError('Invalid native terminal parent, serial or capacity')
            self.serials.add(row[8])
            self.pending[(row[2], row[8])] = row
            return None
        intent = self.pending.pop((row[7], row[8]), None)
        if intent is None or intent[7] != row[2] or intent[4:7] != unit:
            raise InstrumentationError('Unmatched native terminal wait identity')
        return unit, int(row[9])

    def finish(self):
        """A lost or omitted native completion is a refused process receipt."""
        if self.pending:
            raise InstrumentationError('Unretired native terminal intent')


class NativeBashProfile:
    """Validate source-pinned native syntax and independent runtime facts."""

    COMPONENT = 'roles/bind9/files/named-healthcheck.sh'
    SOURCE_SHA256 = '983dfc4e4c1555ece299a43fd77a62c70ca2177b97129f1d57d8b1f66ab4172d'
    INVENTORY_SHA256 = '6c525cc8c5a71eafeaa7c5f0b951263729f8984eddc51a9b2eebee0357b07c06'
    PATCH_SHA256 = '335e16f0b6b0a1e74e72fdba6ddb420e0dd6606709bc75e8cb8581909a1bdc14'
    METRICS = ('statements', 'branches', 'functions', 'lines', 'conditions')
    EVENT_MODE = 'sets'
    INVENTORY_NAME = 'health-profile.json'
    DECLARATION_EVENTS = (('B7', 'D'),)
    MAX_INVENTORY_BYTES = 131072
    FACT_EVENTS = None

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.source = self.root / self.COMPONENT
        if hashlib.sha256(self.source.read_bytes()).hexdigest() != self.SOURCE_SHA256:
            raise InstrumentationError('Script changes require a reviewed native parser profile')
        inventory = self.root / 'tools/quality/native_bash' / self.INVENTORY_NAME
        raw = inventory.read_bytes()
        if len(raw) > self.MAX_INVENTORY_BYTES or hashlib.sha256(raw).hexdigest() != self.INVENTORY_SHA256:
            raise InstrumentationError('The reviewed native Bash inventory changed')
        data = json.loads(raw.decode('utf-8'))
        if (data.get('schema') != 1 or data.get('component') != self.COMPONENT or
                data.get('source_sha256') != self.SOURCE_SHA256 or data.get('patch_sha256') != self.PATCH_SHA256):
            raise InstrumentationError('The native Bash profile describes a different source or parser')
        self.declarations = {tuple(value) for value in data['declarations']}
        if len(self.declarations) != len(data['declarations']):
            raise InstrumentationError('Duplicate native Bash declaration')
        for value in self.declarations:
            BashEventSchema.check(value[:2] + ('1', self.SOURCE_SHA256) + value[2:], self.SOURCE_SHA256)
            if not self.is_declaration(value):
                raise InstrumentationError('The native Bash inventory contains runtime facts')
        self.build_units()

    def build_units(self):
        """Derive each denominator from its own complete native declarations."""
        self.commands = self.declared('B2', 'I')
        self.statement_units = {unit for unit in self.commands if unit[1] != '6'}
        self.function_units = {unit for unit in self.commands if unit[1] == '7'}
        self.controls = self.declared('B7', 'D')
        self.decisions = {unit for unit in self.declared('B3', 'I') if unit[1] in ('101', '102')}
        self.leaves = {unit for unit in self.declared('B3', 'I') if unit[1] in ('103', '104')}
        self.statuses = self.declared('B9', 'I')
        if not self.statuses <= self.commands or not {unit[:3] for unit in self.controls} <= self.commands:
            raise InstrumentationError('Native Bash predicates have no declared command origin')
        self.units = {name: set() for name in self.METRICS}
        self.units['statements'] = self.statement_units.copy()
        self.units['functions'] = self.function_units.copy()
        self.units['lines'] = {int(unit[0]) for unit in self.statement_units}
        self.units['branches'] = {('control',) + unit + (outcome,) for unit in self.controls for outcome in (False, True)}
        self.units['branches'].update(('boolean',) + unit + (outcome,) for unit in self.decisions for outcome in (False, True))
        self.units['conditions'] = {('predicate',) + unit + (outcome,) for unit in self.leaves for outcome in (False, True)}
        self.units['conditions'].update(('status',) + unit + (outcome,) for unit in self.statuses for outcome in (False, True))

    def declared(self, family, event):
        return {row[2:] for row in self.declarations if row[:2] == (family, event)}

    def is_declaration(self, row):
        """Keep native syntax metadata separate from all execution facts."""
        return row[1] == 'I' or tuple(row[:2]) in self.DECLARATION_EVENTS

    def observe(self, row, hits):
        """Admit one runtime fact against this profile's independent units."""
        family, event = row[:2]
        unit = row[4:7]
        if family == 'B1' and event == 'C':
            if ('B1', 'I') + unit not in self.declarations:
                raise InstrumentationError('Unknown native legacy predicate observation')
        elif family == 'B2' and event in ('S', 'F'):
            metric = 'statements' if event == 'S' else 'functions'
            if unit not in self.units[metric]:
                raise InstrumentationError('Unknown or misclassified native Bash entry')
            hits[metric].add(unit)
        elif family == 'B2' and event == 'R':
            value = ('control',) + row[4:8] + (row[8] == '1',)
            if value not in self.units['branches']:
                raise InstrumentationError('Unknown native Bash control outcome')
            hits['branches'].add(value)
        elif family == 'B3':
            metric, label = ('branches', 'boolean') if event == 'R' else ('conditions', 'predicate')
            value = (label,) + unit + (row[7] == '1',)
            if value not in self.units[metric]:
                raise InstrumentationError('Unknown native Bash boolean outcome')
            hits[metric].add(value)
        elif family == 'B8':
            if unit not in self.commands:
                raise InstrumentationError('Native Bash command status has no source origin')
            if event == 'R' and unit in self.statuses:
                hits['conditions'].add(('status',) + unit + (int(row[7]) == 0,))
        else:
            raise InstrumentationError('Unsupported event in the reviewed Bash profile')

    def check_process(self, rows, pid, returncode, syntax=False):
        """Recompute hits from authentic facts; no false outcome is inferred."""
        if type(pid) is not int or pid < 1 or type(returncode) is not int or not 0 <= returncode <= 255:
            raise InstrumentationError('Invalid native Bash process identity or result')
        if not isinstance(rows, list) or len(rows) > 100000:
            raise InstrumentationError('Missing or excessive native Bash process facts')
        states, declared = {}, set()
        terminal = None
        hits = {name: set() for name in self.METRICS}
        total = 0
        for supplied in rows:
            row = BashEventSchema.check(supplied, self.SOURCE_SHA256)
            total += sum(len(value) + 1 for value in row)
            if total > 33554432:
                raise InstrumentationError('Excessive native Bash process fact bytes')
            family, event, process = row[:3]
            state = states.setdefault(process, {'header': False, 'mode': False, 'exit': None})
            if state['exit'] is not None:
                raise InstrumentationError('Native Bash event follows its process exit')
            if (family, event) == ('B1', 'H'):
                if state['header']:
                    raise InstrumentationError('Duplicate native Bash process header')
                state['header'] = True
                continue
            if not state['header']:
                raise InstrumentationError('Native Bash event precedes its process header')
            if (family, event) == ('B5', 'M'):
                if state['mode']:
                    raise InstrumentationError('Duplicate native Bash process mode')
                if row[4] != self.EVENT_MODE:
                    raise InstrumentationError('Native Bash observation mode differs from its metric profile')
                state['mode'] = True
                continue
            if not state['mode']:
                raise InstrumentationError('Native Bash event precedes its exact fact mode')
            if (family, event) == ('B1', 'E'):
                state['exit'] = int(row[5])
                continue
            if self.FACT_EVENTS is not None and (family, event) not in self.FACT_EVENTS:
                raise InstrumentationError('Native Bash entry profile cannot admit another metric')
            declaration = row[:2] + row[4:]
            if self.is_declaration(row):
                if declaration not in self.declarations:
                    raise InstrumentationError('Undeclared native Bash parser unit')
                declared.add(declaration)
                continue
            if syntax:
                raise InstrumentationError('Syntax-only Bash inventory contains runtime execution')
            if family == 'B10':
                if terminal is None:
                    if not hasattr(self, 'statuses'):
                        raise InstrumentationError('Native terminal metric profile is missing')
                    terminal = NativeTerminalPairs(self.statuses, pid)
                completed = terminal.observe(row, states)
                if completed is not None:
                    unit, status = completed
                    hits['conditions'].add(('status',) + unit + (status == 0,))
            else:
                self.observe(row, hits)
        if terminal is not None:
            terminal.finish()
        main = states.get(str(pid))
        if main is None or not main['mode'] or main['exit'] != returncode:
            raise InstrumentationError('Native Bash process receipt is incomplete or has a different result')
        if any(not state['header'] or not state['mode'] for state in states.values()):
            raise InstrumentationError('Incomplete native Bash subprocess provenance')
        if syntax and (len(states) != 1 or declared != self.declarations):
            raise InstrumentationError('Native Bash static inventory differs from the reviewed complete units')
        hits['lines'] = {int(unit[0]) for unit in hits['statements']}
        return hits


class NativeHealthProfile(NativeBashProfile):
    """Full independent metrics for the reviewed periodic health script."""


class NativeLoginPathProfile(NativeBashProfile):
    """Measure the exact published PATH script, including native expansions.

    Each case alternative is a branch target. The literal unquoted default
    pattern is unconditional syntax, not an elementary boolean operand. Its
    native match event is still checked and cannot report a false outcome.
    Parameter operators each declare both choices and elementary outcomes.
    """

    COMPONENT = 'roles/bind9/files/bind9-sbin-path.sh'
    SOURCE_SHA256 = '4d84bfdd178e157c6f5dded101b06f9a13f534fbac700b42908fd33195911e7c'
    INVENTORY_NAME = 'login-path-profile.json'
    INVENTORY_SHA256 = 'c25023a1c524f6c5162e6955617b9356b30d996f64be61e89097f8862a9eafe0'
    DECLARATION_EVENTS = NativeBashProfile.DECLARATION_EVENTS + (('B6', 'P'),)

    def build_units(self):
        super().build_units()
        cases = self.declared('B4', 'I')
        self.arms = {unit for unit in cases if unit[1] == '201'}
        self.patterns = {unit for unit in cases if unit[1] == '202'}
        metadata = self.declared('B6', 'P')
        if ({unit[:3] for unit in metadata} != self.patterns or
                len(metadata) != len(self.patterns)):
            raise InstrumentationError('Case patterns lack their exact native syntax metadata')
        self.unconditional = {unit[:3] for unit in metadata if unit[3] == '1'}
        self.parameters = self.declared('B0', 'I')
        self.units['branches'].update(('case-arm',) + unit for unit in self.arms)
        self.units['conditions'].update(('case-pattern',) + unit + (outcome,)
            for unit in self.patterns - self.unconditional for outcome in (False, True))
        for metric in ('branches', 'conditions'):
            self.units[metric].update(('parameter',) + unit + (outcome,)
                for unit in self.parameters for outcome in (False, True))

    def observe(self, row, hits):
        family, event = row[:2]
        unit = row[4:7]
        if family == 'B0':
            metric = 'branches' if event == 'R' else 'conditions'
            value = ('parameter',) + unit + (row[7] == '1',)
            if value not in self.units[metric]:
                raise InstrumentationError('Unknown native parameter-choice outcome')
            hits[metric].add(value)
        elif family == 'B4' and event == 'A':
            value = ('case-arm',) + unit
            if value not in self.units['branches']:
                raise InstrumentationError('Unknown native case alternative')
            hits['branches'].add(value)
        elif family == 'B4' and event == 'C':
            if unit not in self.patterns:
                raise InstrumentationError('Unknown native case pattern')
            if unit in self.unconditional:
                if row[7] != '1':
                    raise InstrumentationError('Unconditional native default pattern reported false')
            else:
                hits['conditions'].add(('case-pattern',) + unit + (row[7] == '1',))
        else:
            super().observe(row, hits)


class NativeWordOwnerCensus:
    """Verify native source bindings without granting any execution credit."""

    def __init__(self, words, final, commands):
        latest, census = {}, {}
        for site, owner, shape, rank in sorted(words, key=lambda value: (value[0], int(value[3]))):
            value = (owner, shape, int(rank))
            previous = latest.get(site)
            if previous and (previous[1] != shape or (previous[2] == value[2] and previous != value)):
                raise InstrumentationError('Conflicting native word provenance')
            if previous is None or previous[2] < value[2]:
                latest[site] = value
        for site, owner, shape, rank in final:
            if site in census:
                raise InstrumentationError('Duplicate native substitution source identity')
            census[site] = (owner, shape, int(rank))
        source_ids = {unit[2] for unit in commands}
        if (len(census) > 4096 or latest != census or any(site not in source_ids for site in census)):
            raise InstrumentationError('Incomplete native word-owner census')
        if len({value[:2] for value in census.values()}) != len(census):
            raise InstrumentationError('Ambiguous native word-owner substitution')
        self.bindings = census


class NativeCompleteRoleProfile(NativeLoginPathProfile):
    """Compose source-pinned command, case, arithmetic and parameter metrics.

    Separate native syntax and actual results remain independently checked.
    Legacy root facts validate provenance without duplicating arithmetic hits.
    """

    PATCH_SHA256 = '335e16f0b6b0a1e74e72fdba6ddb420e0dd6606709bc75e8cb8581909a1bdc14'
    EVENT_MODE = 'decision_sets'
    MAX_INVENTORY_BYTES = 1048576
    STATIC_EVENTS = frozenset((('B2', 'I'), ('B3', 'I'), ('B4', 'I'),
        ('B6', 'P'), ('B7', 'D'), ('B9', 'I'), ('W1', 'I'), ('W2', 'I'),
        ('W3', 'I'), ('W4', 'I'), ('W5', 'I'), ('W6', 'I'), ('A1', 'I'),
        ('A1', 'P'), ('A2', 'I'), ('A2', 'T'), ('A3', 'L'), ('A4', 'I'),
        ('P3', 'L'), ('P4', 'I')))
    EXPRESSION_RUNTIME = frozenset((('A1', 'R'), ('A2', 'V'), ('A2', 'G'), ('P4', 'C'), ('P4', 'R')))
    FACT_EVENTS = STATIC_EVENTS | EXPRESSION_RUNTIME | frozenset((
        ('B1', 'C'), ('B2', 'S'), ('B2', 'F'), ('B2', 'R'), ('B3', 'C'),
        ('B3', 'R'), ('B4', 'A'), ('B4', 'C'), ('B8', 'R'), ('B8', 'O'),
        ('B10', 'T'), ('B10', 'W')))

    def is_declaration(self, row):
        return tuple(row[:2]) in self.STATIC_EVENTS

    def build_units(self):
        super().build_units()
        self.bindings = NativeWordOwnerCensus(self.declared('W1', 'I'), self.declared('W2', 'I'), self.commands).bindings
        old_kinds = {('B2', 'I'), ('W1', 'I'), ('W2', 'I')}
        entry_bytes = json.dumps(sorted(row for row in self.declarations if row[:2] in old_kinds),
                                 separators=(',', ':')).encode('ascii')
        # The independent earlier census is retained as a fingerprint, rather
        # than shipping a duplicate inventory or a second incomplete profile.
        if hashlib.sha256(entry_bytes).hexdigest() != self.ENTRY_CENSUS_SHA256:
            raise InstrumentationError('Existing native entry/owner inventory changed')
        syntax = [list(row[:2] + ('1', self.SOURCE_SHA256) + row[2:]) for row in self.declarations]
        self.arithmetic = ArithmeticBooleanModel(self.SOURCE_SHA256, syntax)
        if not {row[10] for row in syntax if row[:2] == ['A3', 'L']} <= set(self.arithmetic.roots):
            raise InstrumentationError('Native arithmetic lexical site lacks a complete parsed tree')
        self.parameters_model = ParameterBooleanModel(self.SOURCE_SHA256, syntax)
        self.lexical = {(row[0], row[-1]): tuple(row) for row in syntax
                        if tuple(row[:2]) in (('A3', 'L'), ('P3', 'L'))}
        self.check_lexical_census(syntax)
        for metric in ('branches', 'conditions'):
            self.units[metric].update(('arithmetic',) + unit for unit in self.arithmetic.units[metric])
            self.units[metric].update(('selector',) + unit for unit in self.parameters_model.units)

    @staticmethod
    def check_lexical_census(rows):
        """Require every lexical source site to have its complete final owner."""
        for lexical, initial, final in (('A3', 'W3', 'W4'), ('P3', 'W5', 'W6')):
            sites, latest, census = {}, {}, {}
            for row in rows:
                family, event = row[:2]
                if family == lexical and event == 'L':
                    value = (row[4], row[9])
                    if row[10] in sites and sites[row[10]] != value:
                        raise InstrumentationError('Conflicting native lexical identity')
                    sites[row[10]] = value
                elif family in (initial, final) and event == 'I':
                    target = latest if family == initial else census
                    value = tuple(row[5:])
                    previous = target.get(row[4])
                    if previous and (family == final or previous[1] != value[1] or previous[3] != value[3] or
                                     (previous[2] == value[2] and previous != value)):
                        raise InstrumentationError('Conflicting native lexical owner census')
                    if previous is None or int(previous[2]) < int(value[2]):
                        target[row[4]] = value
            if set(sites) != set(census) or latest != census:
                raise InstrumentationError('Incomplete retained native lexical census')
            if any((owner[3], owner[1]) != sites[site] for site, owner in census.items()):
                raise InstrumentationError('Native lexical line or retained text changed')

    def observe(self, row, hits):
        if tuple(row[:2]) not in self.EXPRESSION_RUNTIME:
            super().observe(row, hits)

    def check_process(self, rows, pid, returncode, syntax=False):
        """Use actual collector PID provenance; never infer a missing result."""
        if not isinstance(rows, list) or len(rows) > 100000:
            raise InstrumentationError('Missing or excessive native Bash process facts')
        compared = []
        for supplied in rows:
            row = BashEventSchema.check(supplied, self.SOURCE_SHA256)
            if tuple(row[:2]) in (('A3', 'L'), ('P3', 'L')):
                expected = self.lexical.get((row[0], row[-1]))
                # A parser allocation serial is local to one process/reparse.
                # All stable source fields must match before adapting it for
                # the shared inventory comparison. Raw evidence stays intact.
                if expected is None or row[4:5] + row[6:] != expected[4:5] + expected[6:]:
                    raise InstrumentationError('Runtime lexical source metadata changed')
                row = row[:5] + (expected[5],) + row[6:]
            compared.append(row)
        hits = super().check_process(compared, pid, returncode, syntax=syntax)
        arithmetic = self.arithmetic.observe(rows)
        for metric in ('branches', 'conditions'):
            hits[metric].update(('arithmetic',) + unit for unit in arithmetic[metric])
        for unit in self.parameters_model.observe(rows):
            for metric in ('branches', 'conditions'):
                hits[metric].add(('selector',) + unit)
        completed = {(row[2], row[4], row[8]) for row in rows
                     if tuple(row[:2]) == ('A2', 'G') and row[7] == '1'}
        for row in rows:
            if tuple(row[:2]) == ('A1', 'R'):
                if (row[4] not in self.arithmetic.roots or int(row[5]) != self.arithmetic.lines[row[4]] or
                        (row[6] == '1' and (row[2], row[4], row[7]) not in completed) or
                        (row[6] == '0' and row[7] != '0')):
                    raise InstrumentationError('Legacy arithmetic result lacks its same-PID native completion')
        if any(hits[metric] - self.units[metric] for metric in hits):
            raise InstrumentationError('Native outcome is outside its full independent denominator')
        return hits


class NativeFrontProfile(NativeCompleteRoleProfile):
    """Whole FRONT source with five independent native metrics."""

    COMPONENT = 'roles/bind9/files/bind-front-routes.sh'
    SOURCE_SHA256 = '735a0ba1dc0080b85b650aae54940ed754f87075c0a0bf7242fe24b3407c0b85'
    ENTRY_CENSUS_SHA256 = '580add79cb2b81a08ed216f45291e53a632447514640a42bf688d6bc2b624dc0'
    INVENTORY_NAME = 'bind-front-routes-profile.json'
    INVENTORY_SHA256 = '4b1ff1c1fda2aff6de0f9717fb0abbb1fd3dee8ac94230f82623aa839d405ba1'


class NativeConfigtoolProfile(NativeCompleteRoleProfile):
    """Whole archive/AXFR source with five independent native metrics."""

    COMPONENT = 'roles/bind9/files/bind-configtool.sh'
    SOURCE_SHA256 = 'c9d84ecf4261274867b7e655c5f246b41f05629e389fffd9fc1b9193aa280b60'
    ENTRY_CENSUS_SHA256 = 'c2fa15b00a28c78501c1121ed569ff0124d1bb84d2b764ab40efbe719daed8aa'
    INVENTORY_NAME = 'bind-configtool-profile.json'
    INVENTORY_SHA256 = '2c4578b85c2201cbdd4ef4dd57019034194f2d15193a036745043313f1ffc19e'


def native_profiles(root):
    """Admit reviewed source profiles that actually belong to this project."""
    result = {profile.COMPONENT: profile(root) for profile in
        (NativeHealthProfile, NativeFrontProfile, NativeConfigtoolProfile, NativeLoginPathProfile)
        if (Path(root) / profile.COMPONENT).is_file()}
    if not result:
        raise InstrumentationError('No reviewed native Bash source profile is present')
    return result
