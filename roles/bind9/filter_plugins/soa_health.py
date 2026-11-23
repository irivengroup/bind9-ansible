"""Exact SOA health assertions using reconciled primary zone serials."""
import re


class SoaHealth:
    """Reject substring matches, nonauthoritative answers and ambiguous SOAs."""

    @staticmethod
    def _answer_rows(output):
        """Extract answer-only rows from the caller's explicit dig output mode."""
        return [line for line in output.splitlines() if line.strip() and not line.startswith(';')]

    @staticmethod
    def response_complete(output):
        """Require one complete NOERROR answer, without implicit TCP fallback.

        Callers use dig +noall +comments +answer: noncomment lines are exactly
        the advertised answer records. Missing headers/counts fail closed.
        """
        if not isinstance(output, str) or len(output) > 1048576 or '\x00' in output:
            return False
        statuses = re.findall(r'\bstatus: ([A-Z]+)\b', output)
        headers = re.findall(r'(?m)^;; flags: ([^;\n]+);[^\n]*\bANSWER: ([0-9]+)\b', output)
        if statuses != ['NOERROR'] or len(headers) != 1:
            return False
        flags, count = headers[0]
        rows = SoaHealth._answer_rows(output)
        return 'tc' not in flags.split() and len(rows) > 0 and int(count) == len(rows)

    @staticmethod
    def checked_serial(output, domain):
        """Extract named-checkzone's serial for the exact successfully loaded zone."""
        pattern = r'(?ami)^zone ' + re.escape(domain.rstrip('.')) + r'/IN: loaded serial ([0-9]+)\s*$'
        matches = re.findall(pattern, output)
        # The matched ASCII digit grammar already guarantees an unsigned value.
        if len(matches) != 1 or int(matches[0]) > 4294967295:
            raise ValueError('Missing/ambiguous checked SOA serial for ' + domain)
        return int(matches[0])

    @staticmethod
    def matches(output, domain, expected):
        """Require one complete authoritative SOA; compare its serial when known.

        An explicit None is reserved for primary readiness before the primary's
        serial has been read. Its owner, record grammar and serial bounds still
        require the same checks as later replication comparisons.
        """
        # Word boundaries also match hyphenated strings such as aa-invalid.
        # The authoritative bit must be a complete token in the header field.
        if (not SoaHealth.response_complete(output) or len(SoaHealth._answer_rows(output)) != 1 or
                not re.search(r'(?m)^;; flags:[ \t]+(?:[a-z]+[ \t]+)*aa(?:[ \t]|;)', output)):
            return False
        pattern = (r'(?ami)^' + re.escape(domain.rstrip('.')) +
                   r'\.?\s+[0-9]+\s+IN\s+SOA\s+\S+\s+\S+\s+([0-9]+)\s+'
                   r'[0-9]+\s+[0-9]+\s+[0-9]+\s+[0-9]+\s*$')
        matches = re.findall(pattern, output)
        return (len(matches) == 1 and int(matches[0]) <= 4294967295 and
                (expected is None or int(matches[0]) == int(expected)))


class FilterModule:
    """Expose exact serial extraction and answer comparison to Ansible."""

    def filters(self):
        return {'checked_soa_serial': SoaHealth.checked_serial, 'soa_matches': SoaHealth.matches,
                'dns_response_complete': SoaHealth.response_complete}
