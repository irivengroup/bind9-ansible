"""Validate native arithmetic syntax and actual elementary Boolean outcomes.

Logical operands and ternary selectors demand Boolean values. Numeric operands
are not predicates by themselves. Comma and plain assignment forward the right
value; their left operand is not automatically a predicate. Only already
evaluated native nodes with same-process completed roots can supply outcomes.
"""
from collections import Counter

from bind9_coverage import InstrumentationError


class NativeExpressionSchema:
    """Bound native lexical, arithmetic and parameter facts before use."""

    FAMILIES = frozenset(('A1', 'A2', 'A3', 'A4', 'P3', 'P4', 'W3', 'W4', 'W5', 'W6'))
    LENGTHS = {('A1', 'I'): 8, ('A1', 'P'): 7, ('A1', 'R'): 8,
        ('A2', 'I'): 11, ('A2', 'T'): 7, ('A2', 'V'): 13, ('A2', 'G'): 9,
        ('A3', 'L'): 11, ('A4', 'I'): 7, ('P3', 'L'): 11,
        ('P4', 'I'): 9, ('P4', 'C'): 9, ('P4', 'R'): 9,
        ('W3', 'I'): 9, ('W4', 'I'): 9, ('W5', 'I'): 9, ('W6', 'I'): 9}

    @classmethod
    def check(cls, row, schema):
        """Reject unknown grammar and unsupported outcomes; accept no raw mode."""
        family, event = row[:2]
        if cls.LENGTHS.get((family, event)) != len(row):
            raise InstrumentationError('Unknown native expression event schema')
        number, digest = schema.number, schema.DIGEST.fullmatch
        if family in ('A3', 'P3'):
            for index, maximum in ((4, 2147483647), (5, 1000000), (6, 2048), (7, 1048576), (8, 1048576)):
                number(row[index], minimum=1, maximum=maximum)
            if not digest(row[9]) or not digest(row[10]):
                raise InstrumentationError('Native lexical site lacks its retained source identity')
        elif family in ('W3', 'W4', 'W5', 'W6'):
            if any(not digest(value) for value in row[4:7]):
                raise InstrumentationError('Native lexical owner lacks its retained source identity')
            number(row[7], minimum=1, maximum=16)
            number(row[8], minimum=1)
        else:
            if not digest(row[4]):
                raise InstrumentationError('Unattributed native expression')
            number(row[5], minimum=1)
            if family == 'P4':
                number(row[6], maximum=255)
                number(row[7], maximum=1)
                number(row[8], maximum=1)
                if event != 'I' and row[6] not in ('43', '45', '61', '63'):
                    raise InstrumentationError('Native parameter result is not a selector')
            elif family == 'A4':
                number(row[6], maximum=5)
            elif family == 'A1':
                if event == 'I':
                    number(row[6], maximum=1048576)
                    number(row[7], maximum=127)
                elif event == 'P':
                    number(row[6], maximum=2)
                else:
                    number(row[6], maximum=1)
                    number(row[7], maximum=1)
            elif event in ('I', 'V'):
                identity = number(row[6], minimum=1, maximum=8192)
                number(row[7], minimum=1, maximum=1151)
                for child in row[8:11]:
                    number(child, maximum=identity - 1)
                for bit in row[11:]:
                    number(bit, maximum=1)
            else:
                number(row[6], maximum=8192)
                for bit in row[7:]:
                    number(bit, maximum=1)
                if event == 'G' and row[7] == '0' and (row[6] != '0' or row[8] != '0'):
                    raise InstrumentationError('Invalid native evaluation cannot supply an outcome')
        return tuple(row)


class ParameterBooleanModel:
    """Require exact authored selector metadata and paired native outcomes."""

    def __init__(self, source, rows):
        self.source = source
        sites = {row[10]: row[4] for row in rows if tuple(row[:2]) == ('P3', 'L')}
        declarations = [row for row in rows if tuple(row[:2]) == ('P4', 'I')]
        self.heads = {row[4]: tuple(row[5:]) for row in declarations}
        if (len(self.heads) != len(declarations) or set(self.heads) != set(sites) or
                any(row[3] != source for row in rows) or
                any(value[-1] != '1' or value[0] != sites[site] for site, value in self.heads.items())):
            raise InstrumentationError('Incomplete or unparsed native parameter inventory')
        self.selectors = {site: value[:3] for site, value in self.heads.items()
                          if value[1] in ('43', '45', '61', '63')}
        self.units = {(site, value) for site in self.selectors for value in (False, True)}

    def observe(self, rows):
        """Match branch/condition facts per PID; never infer an unobserved arm."""
        conditions, branches = Counter(), Counter()
        for row in rows:
            if row[3] != self.source:
                raise InstrumentationError('Native parameter outcome belongs to another source')
            event = tuple(row[:2])
            if event == ('P4', 'I'):
                if self.heads.get(row[4]) != tuple(row[5:]):
                    raise InstrumentationError('Native parameter head differs from complete syntax')
            elif event in (('P4', 'C'), ('P4', 'R')):
                if self.selectors.get(row[4]) != tuple(row[5:8]) or row[8] not in ('0', '1'):
                    raise InstrumentationError('Foreign or malformed native parameter result')
                target = conditions if event[1] == 'C' else branches
                target[(row[2], row[4], row[8] == '1')] += 1
            elif row[0] == 'P4':
                raise InstrumentationError('Unsupported native parameter result remains unknown')
        if conditions != branches:
            raise InstrumentationError('Unpaired native parameter branch/condition outcomes')
        return {(site, truth) for _, site, truth in conditions}

class ArithmeticBooleanModel:
    """Derive independent units from complete syntax; verify actual native facts."""
    LEAVES = {5, 6}
    UNARY = {256 + value for value in (14, 15, 16, 17, 33, 43, 45, 126)}
    BINARY = {512 + value for value in (1, 2, 3, 4, 7, 8, 9, 10, 13, 37, 38, 42, 43, 44, 45, 47, 60, 62, 94, 124)}
    ASSIGNMENT = {1024 + value for value in (9, 10, 13, 37, 38, 42, 43, 45, 47, 61, 94, 124)}

    def __init__(self, source, rows):
        self.source = source
        self.graphs, self.lines, self.purposes, self.roots = ({}, {}, {}, {})
        parsed = {}
        if not all((row[3] == source for row in rows)):
            raise InstrumentationError('Invalid native arithmetic evidence')
        if any(tuple(row[:2]) == ('A2', 'U') for row in rows):
            raise InstrumentationError('Invalid native arithmetic evidence')
        for row in rows:
            event = tuple(row[:2])
            if event == ('A1', 'P'):
                self._unique(parsed, row[4], int(row[6]))
            elif event == ('A2', 'T'):
                self._unique(self.roots, row[4], int(row[6]))
            elif event == ('A4', 'I'):
                self._unique(self.purposes, row[4], int(row[6]))
                self._unique(self.lines, row[4], int(row[5]))
            elif event == ('A2', 'I'):
                graph = self.graphs.setdefault(row[4], {})
                self._unique(graph, int(row[6]), tuple(map(int, row[7:11])))
                self._unique(self.lines, row[4], int(row[5]))
        if not set(parsed) == set(self.roots) == set(self.graphs) == set(self.purposes):
            raise InstrumentationError('Invalid native arithmetic evidence')
        authored = {row[4] for row in rows if tuple(row[:2]) == ('A1', 'I')}
        if authored != set(self.roots):
            raise InstrumentationError('Native arithmetic roots lack their complete authored word census')
        if not set(parsed.values()) <= {1}:
            raise InstrumentationError('Unsupported arithmetic remains unknown')
        self.units = {'branches': set(), 'conditions': set()}
        self.predicates, self.decisions = ({}, {})
        for owner, graph in self.graphs.items():
            count = self.roots[owner]
            if not (0 < count <= 8192 and set(graph) == set(range(1, count + 1))):
                raise InstrumentationError('Invalid native arithmetic evidence')
            if not 0 <= self.purposes[owner] <= 5:
                raise InstrumentationError('Invalid native arithmetic evidence')
            children = []
            for index, (kind, left, right, third) in graph.items():
                if not all((0 <= child < index for child in (left, right, third))):
                    raise InstrumentationError('Invalid native arithmetic evidence')
                if kind in self.LEAVES:
                    if not (left, right, third) == (0, 0, 0):
                        raise InstrumentationError('Invalid native arithmetic evidence')
                elif kind in self.UNARY:
                    if not (left and (not right) and (not third)):
                        raise InstrumentationError('Invalid native arithmetic evidence')
                elif kind in self.BINARY | self.ASSIGNMENT:
                    if not (left and right and (not third)):
                        raise InstrumentationError('Invalid native arithmetic evidence')
                elif not (kind == 780 and left and right and third):
                    raise InstrumentationError('Invalid native arithmetic evidence')
                children.extend((child for child in (left, right, third) if child))
            if not Counter(children) == Counter(range(1, count)):
                raise InstrumentationError('Native graph is not one complete tree')
            predicates, decisions = (set(), {})
            self._classify(graph, count, self.purposes[owner] in (1, 3), predicates, decisions)
            self.predicates[owner], self.decisions[owner] = (predicates, decisions)
            for index in predicates:
                self.units['conditions'].update(((owner, index, value) for value in (False, True)))
            for index in decisions:
                self.units['branches'].update(((owner, index, value) for value in (False, True)))
            if self.purposes[owner] == 3:
                self.units['branches'].update(((owner, 'loop-test', value) for value in (False, True)))

    @staticmethod
    def _unique(mapping, key, value):
        if not (key not in mapping or mapping[key] == value):
            raise InstrumentationError('Conflicting native declaration')
        mapping[key] = value

    def _classify(self, graph, index, demanded, predicates, decisions):
        pending = [(index, demanded)]
        while pending:
            index, demanded = pending.pop()
            kind, left, right, third = graph[index]
            if kind in (519, 520):
                decisions[index] = left
                requests = [(left, True), (right, True)]
            elif kind == 289:
                requests = [(left, True)]
            elif kind == 780:
                decisions[index] = left
                requests = [(left, True), (right, demanded), (third, demanded)]
            elif kind in (556, 1085):
                requests = [(left, False), (right, demanded)]
            else:
                if demanded:
                    predicates.add(index)
                requests = [(child, False) for child in (left, right, third) if child]
            pending.extend(requests)

    def observe(self, rows):
        """Refuse uncompleted/foreign/unknown evidence; never credit placeholders."""
        hits = {'branches': set(), 'conditions': set()}
        completed, values = (set(), [])
        for row in rows:
            if not row[3] == self.source:
                raise InstrumentationError('Invalid native arithmetic evidence')
            event = tuple(row[:2])
            if not event != ('A2', 'U'):
                raise InstrumentationError('Unsupported arithmetic outcome remains unknown')
            if event in (('A2', 'I'), ('A2', 'T'), ('A4', 'I'), ('A1', 'P')):
                owner = row[4]
                if not (owner in self.graphs and int(row[5]) == self.lines[owner]):
                    raise InstrumentationError('Invalid native arithmetic evidence')
                if event == ('A2', 'I'):
                    if not self.graphs[owner].get(int(row[6])) == tuple(map(int, row[7:11])):
                        raise InstrumentationError('Invalid native arithmetic evidence')
                elif event == ('A2', 'T'):
                    if not int(row[6]) == self.roots[owner]:
                        raise InstrumentationError('Invalid native arithmetic evidence')
                elif event == ('A4', 'I'):
                    if not int(row[6]) == self.purposes[owner]:
                        raise InstrumentationError('Invalid native arithmetic evidence')
                elif not row[6] == '1':
                    raise InstrumentationError('Unsupported arithmetic remains unknown')
                continue
            if event not in (('A2', 'V'), ('A2', 'G')):
                continue
            owner = row[4]
            if not (owner in self.graphs and int(row[5]) == self.lines[owner]):
                raise InstrumentationError('Invalid native arithmetic evidence')
            if event == ('A2', 'G'):
                valid, truth = (int(row[7]), int(row[8]))
                if not (valid in (0, 1) and truth in (0, 1)):
                    raise InstrumentationError('Invalid native arithmetic evidence')
                if not valid:
                    if not row[6:9] in (['0', '0', '0'], ('0', '0', '0')):
                        raise InstrumentationError('Invalid native arithmetic evidence')
                    continue
                if not int(row[6]) == self.roots[owner]:
                    raise InstrumentationError('Invalid native arithmetic evidence')
                completed.add((row[2], owner))
                if self.purposes[owner] == 3:
                    hits['branches'].add((owner, 'loop-test', bool(truth)))
            else:
                index = int(row[6])
                if not self.graphs[owner].get(index) == tuple(map(int, row[7:11])):
                    raise InstrumentationError('Invalid native arithmetic evidence')
                if not all((value in ('0', '1') for value in row[11:13])):
                    raise InstrumentationError('Invalid native arithmetic evidence')
                values.append(row)
        for row in values:
            owner, index = (row[4], int(row[6]))
            if not (row[2], owner) in completed:
                raise InstrumentationError('Uncompleted native evaluation cannot supply an outcome')
            if row[11] != '1':
                continue
            truth = row[12] == '1'
            if index in self.predicates[owner]:
                hits['conditions'].add((owner, index, truth))
            for decision, operand in self.decisions[owner].items():
                if index == operand:
                    hits['branches'].add((owner, decision, truth))
        if not all((hits[metric] <= self.units[metric] for metric in hits)):
            raise InstrumentationError('Invalid native arithmetic evidence')
        return hits
