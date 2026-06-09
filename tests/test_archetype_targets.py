"""Unit tests for per-archetype target ranges + scoring."""

import pytest

from poker.archetype_targets import (
    ARCHETYPE_TARGETS,
    MIN_SAMPLE,
    PRODUCTION_ARCHETYPES,
    STAT_LABELS,
    get_targets,
    score_stat,
)

# Stats that intentionally ship with NO target band (render as no_target in the
# review tool, like c-bet) until sim data sets bands — per-street AF (backlog #11).
NO_TARGET_STATS = {'flop_af', 'turn_af', 'river_af'}


def test_every_production_archetype_has_every_stat():
    for arch in PRODUCTION_ARCHETYPES:
        assert arch in ARCHETYPE_TARGETS, f'missing targets for {arch}'
        for stat in STAT_LABELS:
            band = ARCHETYPE_TARGETS[arch].get(stat)
            if stat in NO_TARGET_STATS:
                assert band is None, f'{arch}.{stat} should have no target band'
                continue
            assert band is not None, f'{arch} missing {stat}'
            lo, hi = band
            assert lo <= hi, f'{arch}.{stat} band inverted: {band}'


@pytest.mark.parametrize(
    'actual,band,sample,expected',
    [
        (8.0, (6, 10), 200, 'pass'),  # inside
        (6.0, (6, 10), 200, 'pass'),  # on the boundary
        (11.0, (6, 10), 200, 'warn'),  # just over (margin = 2)
        (22.7, (6, 10), 200, 'fail'),  # way over
        (5.0, (6, 10), 200, 'warn'),  # just under, within margin (lo-2=4)
        (3.0, (6, 10), 200, 'fail'),  # under the margin floor
        (8.0, (6, 10), MIN_SAMPLE - 1, 'low_n'),
        (None, (6, 10), 200, 'no_data'),
    ],
)
def test_score_stat(actual, band, sample, expected):
    assert score_stat(actual, band, sample) == expected


def test_overrides_merge_and_ignore_garbage():
    base = get_targets()
    assert base['tag']['threebet'] == ARCHETYPE_TARGETS['tag']['threebet']

    merged = get_targets('{"tag": {"threebet": [5, 8]}, "bogus": {"x": [1, 2]}}')
    assert merged['tag']['threebet'] == (5.0, 8.0)
    # unknown archetype ignored, other stats untouched
    assert 'bogus' not in merged
    assert merged['tag']['vpip'] == ARCHETYPE_TARGETS['tag']['vpip']


def test_malformed_override_falls_back_to_defaults():
    merged = get_targets('not json{')
    assert merged['maniac']['af'] == ARCHETYPE_TARGETS['maniac']['af']
