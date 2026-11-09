"""A zero denominator cannot conceal invalid or boolean coverage counters."""
import pytest

from test_coverage_gate import Q


@pytest.mark.parametrize('hit,total', [(False, 0), (0, False), (0, 0.0), (0.0, 0),
    (1, 0), (-1, 0), (None, 0), ('0', 0), (0, -1), (True, 1), (1, True)])
def test_invalid_counter_types_or_bounds_are_rejected_even_without_units(hit, total):
    with pytest.raises(ValueError, match='numerator/denominator'):
        Q.CoverageGate.metric(hit, total)
