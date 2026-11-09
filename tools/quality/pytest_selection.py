"""Transport exact pytest identities without putting parameter values in argv.

The pinned pytest parser supports one argument per line in an @file. Keep the
existing 16 MiB manifest and 100,000 identity budgets; refuse line separators
so one collected identity cannot inject another argument or pytest option.
"""
import hashlib
import json
import os
from pathlib import Path
import stat


class TestIdentities:
    """Share bounded, exact identity validation between both paired launchers."""

    @staticmethod
    def validate(nodes):
        if (not isinstance(nodes, list) or not nodes or len(nodes) > 100000 or
                any(not isinstance(node, str) or not node or node.startswith(('-', '@')) or
                    any(character in node for character in ('\0', '\r', '\n')) for node in nodes) or
                len(set(nodes)) != len(nodes)):
            raise ValueError('Malformed, empty or duplicate pytest collection identities')
        return nodes

    @staticmethod
    def junit_identity_sha256(cases):
        """Bind case names without accepting absent or ambiguous identities.

        Empty class names remain valid for native file-level pytest items.
        Outcomes are checked separately; this digest identifies the cases.
        """
        if (not isinstance(cases, (list, tuple)) or not cases or
                any(not isinstance(case, (list, tuple)) or len(case) != 2 or
                    any(not isinstance(value, str) for value in case) for case in cases)):
            raise ValueError('Malformed or ambiguous JUnit testcase identities')
        identities = [tuple(case) for case in cases]
        if len(identities) != len(set(identities)):
            raise ValueError('Malformed or ambiguous JUnit testcase identities')
        raw = json.dumps(sorted(identities), ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        return hashlib.sha256(raw).hexdigest()

    @classmethod
    def selected_junit_sha256(cls, nodes):
        """Use the pinned producer's exact class/parameter/XML name convention.

        Campaigns require default JUnit names. Custom names or prefixes cannot
        silently replace the exact collected identities in either phase.
        """
        import pytest
        if pytest.__version__ != '9.0.3':
            raise ValueError('Exact JUnit identity binding requires pytest 9.0.3')
        from _pytest.junitxml import bin_xml_escape, mangle_test_address
        cases = []
        for node in cls.validate(nodes):
            names = mangle_test_address(node)
            cases.append(('.'.join(names[:-1]), bin_xml_escape(names[-1])))
        return cls.junit_identity_sha256(cases)

    @staticmethod
    def read_json(path):
        descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('Test manifest must be a regular file')
            raw = stream.read(16777217)
        if len(raw) > 16777216:
            raise ValueError('Test manifest exceeds its byte budget')
        return json.loads(raw)

    @classmethod
    def read(cls, path):
        data = cls.read_json(path)
        if not isinstance(data, dict) or set(data) != {'schema', 'nodeids'} or type(data['schema']) is not int or data['schema'] != 1:
            raise ValueError('Malformed exact pytest selection manifest')
        return cls.validate(data['nodeids'])

    @classmethod
    def write(cls, path, nodes):
        raw = (json.dumps({'schema': 1, 'nodeids': cls.validate(nodes)}) + '\n').encode('utf-8')
        if len(raw) > 16777216:
            raise ValueError('Test manifest exceeds its byte budget')
        with Path(path).open('xb') as stream:
            stream.write(raw)

    @classmethod
    def pytest_arguments(cls, path, nodes):
        # Refuse an uninspected parser rather than silently interpreting @file
        # as a different selection. Other QA tools already pin this version.
        import pytest
        if pytest.__version__ != '9.0.3':
            raise ValueError('Exact manifest transport requires pytest 9.0.3')
        with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
            stream.write('\n'.join(cls.validate(nodes)) + '\n')
        return ['@' + str(Path(path).resolve())]
