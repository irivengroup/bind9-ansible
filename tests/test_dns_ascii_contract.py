"""DNS case-insensitivity folds ASCII letters without inventing Unicode aliases."""
import pytest

from test_dns_topology import DnsTopology
from test_management import M
from test_soa_health import SoaHealth


@pytest.mark.parametrize('name', ['K.example.test', 'ſ.example.test', 'İ.example.test'])
def test_unicode_labels_are_not_normalized_into_ascii_zone_names(name):
    with pytest.raises(ValueError):
        DnsTopology.domain(name)
    with pytest.raises(M.ManagementError):
        M.canonical_name(name)


@pytest.mark.parametrize('owner', ['K.example.test', 'ſ.example.test'])
def test_unicode_answer_owner_cannot_prove_an_ascii_question(owner):
    name = 'k.example.test' if owner.startswith('K') else 's.example.test'
    answer = (';; status: NOERROR\n;; flags: qr aa; QUERY: 1, ANSWER: 1\n' + owner +
              '. 60 IN SOA ns.example. hostmaster.example. 12 3600 900 604800 300\n')
    assert not SoaHealth.matches(answer, name, 12)
    assert not M.DnsReply(answer).matches(name + '.', 'SOA', '12')


def test_ascii_uppercase_names_and_answers_keep_the_public_contract():
    assert DnsTopology.domain('K.EXAMPLE.TEST') == 'k.example.test'
    assert M.canonical_name('K.EXAMPLE.TEST') == 'k.example.test.'
    answer = (';; status: NOERROR\n;; flags: qr aa; QUERY: 1, ANSWER: 1\n'
              'K.EXAMPLE.TEST. 60 IN SOA ns.example. hostmaster.example. 12 3600 900 604800 300\n')
    assert SoaHealth.matches(answer, 'k.example.test', 12)
    assert M.DnsReply(answer).matches('k.example.test.', 'SOA', '12')
