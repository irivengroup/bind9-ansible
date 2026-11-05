"""Bounded deterministic grammar checks with independent accept/refuse oracles."""
import random
import string

import pytest

from test_dns_topology import DnsTopology
from test_management import M, ZONE


def test_ascii_zone_names_and_unicode_lookalikes_keep_distinct_identities():
    randomizer = random.Random(4343)
    invalid = ('K', 'ſ', 'İ', 'é', 'а', '\x00', '\n', '\r')
    for index in range(1024):
        label = ''.join(randomizer.choice(string.ascii_letters) for _ in range(randomizer.randint(1, 20)))
        name = label + '.example.test'
        assert DnsTopology.domain(name) == name.lower()
        assert M.canonical_name(name) == name.lower() + '.'
        position = randomizer.randrange(len(label))
        altered = label[:position] + invalid[index % len(invalid)] + label[position + 1:] + '.example.test'
        with pytest.raises(ValueError):
            DnsTopology.domain(altered)
        with pytest.raises(M.ManagementError):
            M.canonical_name(altered)


def test_malformed_ttl_directives_never_disappear_before_the_valid_zone():
    randomizer = random.Random(1035)
    for _ in range(512):
        ttl = str(randomizer.randint(0, 4294967295))
        # A second token is forbidden even if the following $TTL is valid.
        with pytest.raises(M.ManagementError, match=r'Invalid \$TTL'):
            M.ZoneDocument('$TTL ' + ttl + ' extra\n' + ZONE, 'example.test.')
        document = M.ZoneDocument(ZONE.replace('$TTL 300', '$TTL ' + ttl), 'example.test.')
        assert document.serial().text == '41'
        assert document.rrset('www.example.test.', 'A')[0]['ttl'] == ttl
