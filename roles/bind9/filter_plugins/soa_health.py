"""Exact SOA health assertions using reconciled primary zone serials."""
import re


class SoaHealth:
    """Reject substring matches, nonauthoritative answers and ambiguous SOAs."""

    @staticmethod
    def checked_serial(output, domain):
        """Extract named-checkzone's serial for the exact successfully loaded zone."""
        pattern = r'(?mi)^zone ' + re.escape(domain.rstrip('.')) + r'/IN: loaded serial ([0-9]+)\s*$'
        matches = re.findall(pattern, output)
        if len(matches) != 1 or not 0 <= int(matches[0]) <= 4294967295:
            raise ValueError('Missing/ambiguous checked SOA serial for ' + domain)
        return int(matches[0])

    @staticmethod
    def matches(output, domain, expected):
        """Require NOERROR, AA and exactly one SOA with the expected serial."""
        if 'status: NOERROR' not in output or not re.search(r'flags:[^;\n]*\baa\b', output):
            return False
        pattern = (r'(?mi)^' + re.escape(domain.rstrip('.')) +
                   r'\.?\s+[0-9]+\s+IN\s+SOA\s+\S+\s+\S+\s+([0-9]+)\s+'
                   r'[0-9]+\s+[0-9]+\s+[0-9]+\s+[0-9]+\s*$')
        matches = re.findall(pattern, output)
        return len(matches) == 1 and int(matches[0]) == int(expected)


class FilterModule:
    """Expose exact serial extraction and answer comparison to Ansible."""

    def filters(self):
        return {'checked_soa_serial': SoaHealth.checked_serial, 'soa_matches': SoaHealth.matches}
