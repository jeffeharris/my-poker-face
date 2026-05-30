#!/usr/bin/env python3
"""Unit tests for the preflop leak finder (pure core — no DB)."""

from flask_app.services.coach_leaks import (
    PreflopLeak,
    compute_preflop_leaks,
    position_to_group,
    reference_plays,
)


def _decisions(canon, position, action, n):
    return [{'canon': canon, 'position': position, 'action': action} for _ in range(n)]


# A controllable reference: only 'AKs' is "in range" anywhere.
def _fake_ref(canon, group):
    return canon == 'AKs'


class TestLeakClassification:
    def test_too_loose_playing_a_fold_hand(self):
        # Voluntarily playing 72o (not in ref) from UTG, repeatedly.
        rep = compute_preflop_leaks(_decisions('72o', 'UTG', 'call', 6), reference=_fake_ref)
        loose = [lk for lk in rep.leaks if lk.leak_type == 'too_loose']
        assert len(loose) == 1
        assert loose[0].canon == '72o'
        assert loose[0].position_group == 'early'
        assert loose[0].severity == 6
        assert loose[0].vpip_pct == 100.0

    def test_folding_an_in_range_hand_is_not_graded(self):
        # too_tight is intentionally NOT flagged: the reference is an opening
        # range and we can't tell opens from correct folds-to-a-raise.
        rep = compute_preflop_leaks(_decisions('AKs', 'BTN', 'fold', 5), reference=_fake_ref)
        assert rep.leaks == []
        assert all(lk.leak_type != 'too_tight' for lk in rep.leaks)

    def test_in_range_and_played_is_not_a_leak(self):
        rep = compute_preflop_leaks(_decisions('AKs', 'BTN', 'raise', 5), reference=_fake_ref)
        assert rep.leaks == []

    def test_fold_hand_folded_is_not_a_leak(self):
        rep = compute_preflop_leaks(_decisions('72o', 'UTG', 'fold', 5), reference=_fake_ref)
        assert rep.leaks == []

    def test_min_sample_gate(self):
        # Only 2 plays of a fold-hand — below the default gate, not flagged.
        rep = compute_preflop_leaks(_decisions('72o', 'UTG', 'call', 2), reference=_fake_ref)
        assert rep.leaks == []
        assert rep.total_decisions == 2

    def test_ranked_worst_first(self):
        decisions = _decisions('72o', 'UTG', 'call', 8) + _decisions('J5o', 'CO', 'call', 4)
        rep = compute_preflop_leaks(decisions, reference=_fake_ref)
        assert [lk.canon for lk in rep.leaks] == ['72o', 'J5o']  # higher severity first

    def test_position_summary(self):
        rep = compute_preflop_leaks(_decisions('72o', 'UTG', 'call', 6), reference=_fake_ref)
        summary = rep.by_position_summary['early']
        assert summary['decisions'] == 6
        assert summary['loose_plays'] == 6  # all 6 were voluntary plays of a below-range hand
        assert summary['vpip_pct'] == 100.0
        assert 'reference_vpip_pct' in summary  # context value present


class TestPositionMapping:
    def test_six_max_labels(self):
        assert position_to_group('UTG') == 'early'
        assert position_to_group('HJ') == 'middle'
        assert position_to_group('CO') == 'late'
        assert position_to_group('BTN') == 'late'
        assert position_to_group('SB') == 'blind'
        assert position_to_group('BB') == 'blind'

    def test_long_keys(self):
        assert position_to_group('under_the_gun') == 'early'
        assert position_to_group('button') == 'late'
        assert position_to_group('cutoff') == 'late'
        assert position_to_group('middle_position_1') == 'middle'
        # Blinds must NOT fall through to the RFI mapper (which buckets them LATE).
        assert position_to_group('small_blind_player') == 'blind'
        assert position_to_group('big_blind_player') == 'blind'

    def test_unmappable_is_none(self):
        assert position_to_group('') is None
        assert position_to_group(None) is None
        assert position_to_group('garbage') is None


class TestRealReference:
    def test_premium_in_range_everywhere(self):
        assert reference_plays('AA', 'early') is True
        assert reference_plays('AKs', 'late') is True

    def test_trash_out_of_range_everywhere(self):
        assert reference_plays('72o', 'early') is False
        assert reference_plays('72o', 'late') is False


class TestFormatForPrompt:
    def test_empty_history(self):
        from flask_app.services.coach_leaks import compute_preflop_leaks, format_leaks_for_prompt

        txt = format_leaks_for_prompt(compute_preflop_leaks([]))
        assert 'No preflop history' in txt

    def test_describes_position_and_weakness(self):
        from flask_app.services.coach_leaks import compute_preflop_leaks, format_leaks_for_prompt

        rep = compute_preflop_leaks(_decisions('72o', 'UTG', 'call', 6), reference=_fake_ref)
        txt = format_leaks_for_prompt(rep)
        assert 'PREFLOP PROFILE' in txt
        assert 'Early position' in txt
        assert '72o' in txt
        assert 'WEAKNESS' in txt

    def test_clean_profile_notes_discipline(self):
        from flask_app.services.coach_leaks import compute_preflop_leaks, format_leaks_for_prompt

        rep = compute_preflop_leaks(_decisions('AKs', 'BTN', 'raise', 6), reference=_fake_ref)
        txt = format_leaks_for_prompt(rep)
        assert 'STRENGTH' in txt or 'disciplined' in txt
