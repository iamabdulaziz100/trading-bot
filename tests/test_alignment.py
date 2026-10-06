import itertools

from app.core.alignment import alignment_matrix
from app.models import LONG, NEUTRAL_FILTER, SHORT


def test_full_27_combo_matrix():
    seen = set()
    for w, d, h in itertools.product((1, 0, -1), repeat=3):
        long_ok = (w == 1 and d == 1) or (d == 1 and h == 1)
        short_ok = (w == -1 and d == -1) or (d == -1 and h == -1)
        expected = LONG if long_ok else SHORT if short_ok else NEUTRAL_FILTER
        assert alignment_matrix(w, d, h) == expected, (w, d, h)
        seen.add((w, d, h))
    assert len(seen) == 27


def test_examples():
    assert alignment_matrix(1, 1, -1) == LONG
    assert alignment_matrix(-1, 1, 1) == LONG
    assert alignment_matrix(1, -1, 1) == NEUTRAL_FILTER
    assert alignment_matrix(0, 1, 0) == NEUTRAL_FILTER  # UNDEFINED counts as 0
    assert alignment_matrix(0, -1, -1) == SHORT
    assert alignment_matrix(0, 0, 0) == NEUTRAL_FILTER
