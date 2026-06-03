"""Hand classification for postflop strategy decisions.

Classifies a player's hand into a made-hand tier and draw modifier,
then maps those to a simplified 6-class bucket used by the postflop
strategy table.

The classifier is *board-aware*: it downgrades made hands whose
strength is undermined by board texture (non-nut straights when
higher straights are possible, pairs on paired boards, top pair on
4-Broadway / 4-flush boards, etc.). Downgrades happen here rather
than in each consumer so every downstream rule
(`value_override`, `value_vs_station`, `bluff_catch_override`, the
defense-floor matrix in plan §2) sees the same corrected value.

The richer output (`nut_status`, `danger_flags`) is available via
`classify_hand_full`; the legacy `classify_hand` tuple is a thin
wrapper that returns the downgraded `made_tier` + `draw_modifier`.
"""

from collections import Counter
from dataclasses import dataclass
from types import SimpleNamespace
from typing import FrozenSet, List, Tuple

from poker.board_analyzer import analyze_board_texture
from poker.hand_evaluator import HandEvaluator, _has_straight_draw

RANK_VALUES = {
    '2': 2,
    '3': 3,
    '4': 4,
    '5': 5,
    '6': 6,
    '7': 7,
    '8': 8,
    '9': 9,
    'T': 10,
    'J': 11,
    'Q': 12,
    'K': 13,
    'A': 14,
}

# --- Danger flag names (constants for grep-ability) ----------------
PAIRED_BOARD = 'paired_board'
TRIPS_ON_BOARD = 'trips_on_board'
FOUR_STRAIGHT_BOARD = 'four_straight_board'
FOUR_FLUSH_BOARD = 'four_flush_board'
HIGHER_STRAIGHT_POSSIBLE = 'higher_straight_possible'
HIGHER_FLUSH_POSSIBLE = 'higher_flush_possible'
FULL_HOUSE_POSSIBLE = 'full_house_possible'

# --- Nut status values ---------------------------------------------
NUT_ACTUAL = 'actual_nuts'
NUT_NEAR = 'near_nuts'
NUT_NON_NUT_STRONG = 'non_nut_strong'
NUT_BLUFF_CATCHER = 'bluff_catcher'


@dataclass(frozen=True)
class HandClassification:
    """Full hand classification including board danger and nut status.

    Fields:
        made_tier: 'nuts' | 'strong_made' | 'medium_made' | 'weak_made' | 'air'
            (post-downgrade)
        draw_modifier: 'strong_draw' | 'weak_draw' | 'backdoor' | 'no_draw'
        hand_class: 6-value simplified label from `simplify_hand_class`
            applied to the *downgraded* made_tier
        nut_status: 'actual_nuts' | 'near_nuts' | 'non_nut_strong' | 'bluff_catcher'
        danger_flags: frozenset of danger flag strings (see PAIRED_BOARD etc.)
    """

    made_tier: str
    draw_modifier: str
    hand_class: str
    nut_status: str
    danger_flags: FrozenSet[str]


def _parse_card(card_str: str) -> SimpleNamespace:
    """Parse a card string like 'Ah' into a Card-like object."""
    return SimpleNamespace(value=RANK_VALUES[card_str[0]], suit=card_str[1])


def _classify_made_tier(
    hand_rank: int,
    hole_ranks: List[int],
    board_ranks: List[int],
    community_cards: List[str],
) -> str:
    """Classify the raw made-hand strength tier (pre-downgrade)."""
    # Flush/straight or better
    if hand_rank <= 6:
        return 'nuts'

    # Three of a kind — set vs trips
    if hand_rank == 7:
        if hole_ranks[0] == hole_ranks[1]:
            return 'nuts'  # Set (pocket pair hit the board)
        return 'strong_made'  # Trips (one hole card + board pair)

    # Two pair
    if hand_rank == 8:
        return 'strong_made'

    # One pair
    if hand_rank == 9:
        is_pocket_pair = hole_ranks[0] == hole_ranks[1]
        sorted_board = sorted(board_ranks, reverse=True)

        # Overpair: pocket pair > all board ranks
        if is_pocket_pair and hole_ranks[0] > sorted_board[0]:
            return 'strong_made'

        # Which pair did we make with the board?
        matching_ranks = [r for r in hole_ranks if r in board_ranks]
        if matching_ranks:
            pair_rank = matching_ranks[0]
            other_hole = [r for r in hole_ranks if r != pair_rank]
            kicker = other_hole[0] if other_hole else 0

            # Top pair
            if pair_rank == sorted_board[0]:
                if kicker >= 13:  # A or K kicker
                    return 'strong_made'
                return 'medium_made'

            # Second pair
            if len(sorted_board) >= 2 and pair_rank == sorted_board[1]:
                texture = analyze_board_texture(community_cards)
                category = texture.get('texture_category', 'dry')
                if category in ('dry', 'semi_wet'):
                    return 'medium_made'
                return 'weak_made'

        # Third pair, bottom pair, underpair, etc.
        return 'weak_made'

    # High card / no pair
    return 'air'


def _classify_straight_draw(all_ranks_sorted: List[int]) -> str:
    """Classify straight draw type.

    Returns 'oesd', 'gutshot', or None.
    """
    # Check OESD: 4 ranks spanning exactly 3 (4 consecutive)
    for i in range(len(all_ranks_sorted) - 3):
        if all_ranks_sorted[i + 3] - all_ranks_sorted[i] == 3:
            return 'oesd'

    # Wheel OESD: A-2-3-4
    if 14 in all_ranks_sorted:
        low_ranks = sorted(set([1] + [r for r in all_ranks_sorted if r <= 5]))
        for i in range(len(low_ranks) - 3):
            if low_ranks[i + 3] - low_ranks[i] == 3:
                return 'oesd'

    # Gutshot: 4 in a 5-rank window (from _has_straight_draw)
    if _has_straight_draw(all_ranks_sorted):
        return 'gutshot'

    return None


def _classify_draw_modifier(
    hand_rank: int,
    hole_cards: List[str],
    community_cards: List[str],
) -> str:
    """Classify the draw modifier for the hand."""
    # Completed hands don't have draw modifiers
    if hand_rank <= 6:
        return 'no_draw'

    # River: the board is complete, so no draw can still be live. A 4-flush or
    # open-ender here is *dead* (zero equity to improve) and must not be read as
    # a "strong draw" — that promotion (`strong_made + strong_draw → nuts` in
    # simplify_hand_class) was crediting finished-board hands with phantom
    # equity. See test_river_dead_flush_is_no_draw.
    if len(community_cards) >= 5:
        return 'no_draw'

    all_cards = hole_cards + community_cards
    all_suits = [c[1] for c in all_cards]
    all_ranks = sorted(set(RANK_VALUES[c[0]] for c in all_cards))

    # Flush draw: 4+ of any suit
    suit_counts = Counter(all_suits)
    has_flush_draw = any(count >= 4 for count in suit_counts.values())

    # Straight draw classification
    straight_type = _classify_straight_draw(all_ranks)

    # Combo draw or flush draw or OESD → strong_draw
    if has_flush_draw:
        return 'strong_draw'
    if straight_type == 'oesd':
        return 'strong_draw'

    # Gutshot → weak_draw
    if straight_type == 'gutshot':
        return 'weak_draw'

    # Backdoor flush: 3 of any suit
    has_backdoor = any(count == 3 for count in suit_counts.values())
    if has_backdoor:
        return 'backdoor'

    return 'no_draw'


# --- Board-aware danger / nut-status helpers -----------------------


def _has_four_in_window(ranks: List[int]) -> bool:
    """True if 4 distinct ranks form a 4-consecutive run (including wheel)."""
    unique = sorted(set(ranks))
    for i in range(len(unique) - 3):
        if unique[i + 3] - unique[i] == 3:
            return True
    # Wheel: treat A as 1 for low straight (A-2-3-4)
    if 14 in unique:
        low = sorted(set([1] + [r for r in unique if r <= 5]))
        for i in range(len(low) - 3):
            if low[i + 3] - low[i] == 3:
                return True
    return False


def _max_straight_high_using_board(board_ranks: List[int]) -> int:
    """Highest straight high-card reachable using ≥ 4 of the board ranks
    + 1 (unspecified) opponent hole card.

    A 4-rank window of the board lets opponent complete a higher straight
    with a single hole card, so this captures the case where hero's
    straight can be outranked without opponent needing both hole cards.

    Returns 0 if no 4-rank board window exists.
    """
    unique = sorted(set(board_ranks))
    extended = sorted(set(([1] + unique) if 14 in unique else unique))

    best_high = 0
    for high in range(14, 4, -1):
        window = set(range(high - 4, high + 1))
        if len(window & set(extended)) >= 4:
            best_high = high
            break  # iterate high to low; first match is the highest
    return best_high


def _compute_danger_flags(
    hole_cards: List[str],
    community_cards: List[str],
    hand_rank: int,
    hand_values: List[int],
) -> FrozenSet[str]:
    """Compute board + hand-vs-board danger flags.

    Flags are *strategic* annotations on the situation. They do not
    encode actual hand strength on their own — that's `nut_status`'s
    job. Consumers (e.g., §2 defense floor) read them as dampeners.
    """
    flags = set()
    if len(community_cards) < 3:
        return frozenset()

    board_ranks = [RANK_VALUES[c[0]] for c in community_cards]
    board_suits = [c[1] for c in community_cards]
    rank_counts = Counter(board_ranks)
    suit_counts = Counter(board_suits)

    if any(c >= 2 for c in rank_counts.values()):
        flags.add(PAIRED_BOARD)
    if any(c >= 3 for c in rank_counts.values()):
        flags.add(TRIPS_ON_BOARD)
    if any(c >= 4 for c in suit_counts.values()):
        flags.add(FOUR_FLUSH_BOARD)
    if _has_four_in_window(board_ranks):
        flags.add(FOUR_STRAIGHT_BOARD)

    # Hero has a straight (rank 6); check if a higher straight is reachable
    # given the board has 4 connected ranks (so opponent needs only 1 hole
    # card, not 2, to complete).
    if hand_rank == 6 and FOUR_STRAIGHT_BOARD in flags:
        hero_high = hand_values[0] if hand_values else 0
        max_board_high = _max_straight_high_using_board(board_ranks)
        if max_board_high > hero_high:
            flags.add(HIGHER_STRAIGHT_POSSIBLE)

    # Full house possible: board paired + hero's hand is below FH strength.
    # (Hero already has FH or better → flag is irrelevant.)
    if PAIRED_BOARD in flags and hand_rank > 4:
        flags.add(FULL_HOUSE_POSSIBLE)

    # Higher flush possible: hero made a flush but doesn't hold the top
    # flush cards (and they aren't on the board), so a higher flush is
    # reachable by an opponent with the missing high card of the suit.
    if hand_rank == 5:
        flush_suit = max(suit_counts, key=suit_counts.get)
        hole_flush_ranks = {RANK_VALUES[c[0]] for c in hole_cards if c[1] == flush_suit}
        board_flush_ranks = {RANK_VALUES[c[0]] for c in community_cards if c[1] == flush_suit}
        seen = hole_flush_ranks | board_flush_ranks
        hero_top_flush = max(hole_flush_ranks | {0}, default=0)
        for higher in (14, 13, 12):
            if higher in seen:
                continue
            if higher > hero_top_flush:
                flags.add(HIGHER_FLUSH_POSSIBLE)
                break

    return frozenset(flags)


def _classify_nut_status(
    hand_rank: int,
    hole_ranks: List[int],
    board_ranks: List[int],
    danger_flags: FrozenSet[str],
) -> str:
    """Assign nut_status from hand_rank + danger flags.

    The four labels capture *strategic* nut-ness, not made-hand
    strength. A non-nut straight is `non_nut_strong`; a pair on a
    paired board is `bluff_catcher`; etc.
    """
    # Royal / straight flush / quads — always actual nuts
    if hand_rank <= 3:
        return NUT_ACTUAL

    # Full house — actual nuts unless opp can have a bigger FH or quads
    # (rare; treat as near_nuts on trips boards).
    if hand_rank == 4:
        if TRIPS_ON_BOARD in danger_flags:
            return NUT_NEAR
        return NUT_ACTUAL

    # Flush — actual nuts only if hero holds the nut-suit ace
    if hand_rank == 5:
        if HIGHER_FLUSH_POSSIBLE in danger_flags:
            return NUT_NON_NUT_STRONG
        if FULL_HOUSE_POSSIBLE in danger_flags:
            return NUT_NON_NUT_STRONG
        return NUT_ACTUAL

    # Straight — non-nut if a higher straight is possible
    if hand_rank == 6:
        if HIGHER_STRAIGHT_POSSIBLE in danger_flags:
            return NUT_NON_NUT_STRONG
        if FULL_HOUSE_POSSIBLE in danger_flags:
            return NUT_NON_NUT_STRONG
        return NUT_ACTUAL

    # Three of a kind (set or trips)
    if hand_rank == 7:
        if FULL_HOUSE_POSSIBLE in danger_flags:
            return NUT_NON_NUT_STRONG
        if FOUR_FLUSH_BOARD in danger_flags or FOUR_STRAIGHT_BOARD in danger_flags:
            return NUT_NON_NUT_STRONG
        return NUT_NEAR

    # Two pair
    if hand_rank == 8:
        if FULL_HOUSE_POSSIBLE in danger_flags:
            return NUT_NON_NUT_STRONG
        if FOUR_FLUSH_BOARD in danger_flags or FOUR_STRAIGHT_BOARD in danger_flags:
            return NUT_BLUFF_CATCHER
        return NUT_NON_NUT_STRONG

    # One pair
    if hand_rank == 9:
        # Pair on paired board: hero's "second pair" is the board pair,
        # which everyone shares — hero's hole-card pair is the only edge.
        # Treat as bluff catcher regardless of which pair hero holds.
        if PAIRED_BOARD in danger_flags:
            return NUT_BLUFF_CATCHER
        # Pair on highly coordinated board (4-flush / 4-straight)
        if FOUR_FLUSH_BOARD in danger_flags or FOUR_STRAIGHT_BOARD in danger_flags:
            return NUT_BLUFF_CATCHER
        return NUT_NON_NUT_STRONG

    # High card / air
    return NUT_BLUFF_CATCHER


def _apply_made_tier_downgrade(
    raw_made_tier: str,
    nut_status: str,
    danger_flags: FrozenSet[str],
) -> str:
    """Downgrade `_classify_made_tier`'s raw output based on nut status
    and board danger.

    Downgrade rules (the §1 fix):
    - `nuts` + `non_nut_strong` → `strong_made` (e.g., non-nut straight
      on a 4-straight board)
    - `nuts` + `bluff_catcher` → `medium_made` (e.g., one pair labeled
      as a "set" because the classifier conflates pocket pair + board
      paired, though the made-tier path generally guards this)
    - `strong_made` + `bluff_catcher` → `medium_made` (e.g., top pair on
      a paired or 4-Broadway board) — `strong_made` stays for
      `non_nut_strong` because it's still a value hand
    - Everything else passes through
    """
    if raw_made_tier == 'nuts':
        if nut_status == NUT_NON_NUT_STRONG:
            return 'strong_made'
        if nut_status == NUT_BLUFF_CATCHER:
            return 'medium_made'
        return 'nuts'

    if raw_made_tier == 'strong_made':
        if nut_status == NUT_BLUFF_CATCHER:
            return 'medium_made'
        return 'strong_made'

    return raw_made_tier


# --- Board-play detection ------------------------------------------
#
# The made-tier path credits a player for the *category* of hand they hold
# (two pair, a flush, a straight) without asking how much of that strength is
# theirs versus the board's. On a board that already makes a strong hand by
# itself — e.g. AA22 two pair on the board — a player who "has two pair" may be
# playing the board with a kicker that everyone at the table shares. Crediting
# that as `strong_made`/`nuts` is how Q5 on 2h2cAdThAs got jammed as the nuts.
#
# The fix is board-agnostic: evaluate the naked board and compare it to the
# player's best 7-card hand. Tie → they contributed nothing (`plays_board`);
# same category but a better kicker → marginal shared-hand edge (`kicker_only`).
_PLAYS_BOARD = 'plays_board'
_KICKER_ONLY = 'kicker_only'
_USES_HOLE = 'uses_hole'

# One-step made-tier demotion for `kicker_only` hands (a real but tiny edge
# over a shared board hand — can call, must never value-jam).
_DEMOTE_MADE_TIER = {
    'nuts': 'medium_made',
    'strong_made': 'medium_made',
    'medium_made': 'weak_made',
    'weak_made': 'weak_made',
    'air': 'air',
}


def _board_play_level(hole_cards: List[str], community_cards: List[str]) -> str:
    """How much the hole cards improve on the naked board.

    Only meaningful once the board can stand alone (5 cards); on earlier
    streets the board isn't a complete hand, so returns `uses_hole`.

    Returns:
        'plays_board' — hero's best hand ties the board exactly (no edge).
        'kicker_only' — same made-hand category, hero only has a better kicker.
        'uses_hole'   — hero genuinely improves the board's hand category.
    """
    if len(community_cards) < 5:
        return _USES_HOLE
    board_eval = HandEvaluator(
        [_parse_card(c) for c in community_cards]
    ).evaluate_hand()
    hero_eval = HandEvaluator(
        [_parse_card(c) for c in hole_cards + community_cards]
    ).evaluate_hand()
    board_key = (board_eval['hand_rank'], tuple(board_eval.get('hand_values') or []))
    hero_key = (hero_eval['hand_rank'], tuple(hero_eval.get('hand_values') or []))
    if hero_key == board_key:
        return _PLAYS_BOARD
    if hero_eval['hand_rank'] == board_eval['hand_rank']:
        return _KICKER_ONLY
    return _USES_HOLE


def _two_pair_topped_by_board_pair(
    hand_rank: int,
    hand_values: List[int],
    community_cards: List[str],
) -> bool:
    """True when hero's two pair is *led* by a pair the board makes by itself.

    On a paired board the evaluator promotes a one-pair holding to "two pair"
    by folding in the board's own pair. When that board pair is the *higher* of
    the two (e.g. 3s5s on 3h Qd Ks Tc Th → "tens and threes", where the tens are
    the board's pair), hero's real edge is only the lower pair — everyone with a
    card of the board-pair rank already shares the top pair, so the hand is a
    bluff-catcher, not value. This mirrors the paired-board demotion the
    one-pair branch of `_classify_nut_status` already applies; the made-tier
    path otherwise credits *all* two pair as `strong_made` (hand_rank 8),
    board-blind.

    Hero making the *top* pair with a hole card (e.g. K5 on K-7-7-x → kings &
    sevens, where the kings use a hole card) is genuine top-two value → False.
    `_board_play_level` already handles the case where hero contributes nothing.
    """
    if hand_rank != 8 or len(hand_values) < 2:
        return False
    high_pair_rank = hand_values[0]
    board_rank_counts = Counter(RANK_VALUES[c[0]] for c in community_cards)
    return board_rank_counts.get(high_pair_rank, 0) >= 2


def classify_hand_full(
    hole_cards: List[str],
    community_cards: List[str],
) -> HandClassification:
    """Classify a hand into the full (made_tier, draw_modifier,
    hand_class, nut_status, danger_flags) tuple.

    `made_tier` and `hand_class` reflect the *post-downgrade* values.
    """
    all_card_objs = [_parse_card(c) for c in hole_cards + community_cards]
    result = HandEvaluator(all_card_objs).evaluate_hand()
    hand_rank = result['hand_rank']
    hand_values = result.get('hand_values') or []

    hole_ranks = [RANK_VALUES[c[0]] for c in hole_cards]
    board_ranks = [RANK_VALUES[c[0]] for c in community_cards]

    danger_flags = _compute_danger_flags(
        hole_cards,
        community_cards,
        hand_rank,
        hand_values,
    )
    nut_status = _classify_nut_status(
        hand_rank,
        hole_ranks,
        board_ranks,
        danger_flags,
    )
    raw_made_tier = _classify_made_tier(
        hand_rank,
        hole_ranks,
        board_ranks,
        community_cards,
    )
    made_tier = _apply_made_tier_downgrade(
        raw_made_tier,
        nut_status,
        danger_flags,
    )
    draw_modifier = _classify_draw_modifier(
        hand_rank,
        hole_cards,
        community_cards,
    )

    # Board-play override: a hand whose strength is supplied by the board, not
    # the hole cards, must not be credited as value. Runs after the made-tier
    # path so it can override its (board-blind) verdict. See _board_play_level.
    board_play = _board_play_level(hole_cards, community_cards)
    if board_play == _PLAYS_BOARD:
        # Everyone has this — it is air that happens to "make" the board's hand.
        made_tier = 'air'
        nut_status = NUT_BLUFF_CATCHER
    elif board_play == _KICKER_ONLY and nut_status not in (NUT_ACTUAL, NUT_NEAR):
        # Same made-hand category as the naked board, but hero has better
        # tiebreakers. When those tiebreakers make a genuine nut / near-nut hand
        # — the nut flush on a monotone board, the nut straight on a board that
        # already runs four to a straight — the hole card IS the deciding high
        # card, not a throwaway kicker, so the danger-flag system's `nuts`
        # verdict stands and we leave it alone. Only the marginal case (a
        # non-nut flush/straight that barely edges the shared board hand) is the
        # bluff-catcher Lucille targeted: demote a step and mark bluff_catcher.
        made_tier = _DEMOTE_MADE_TIER[made_tier]
        nut_status = NUT_BLUFF_CATCHER
    elif _two_pair_topped_by_board_pair(hand_rank, hand_values, community_cards):
        # Two pair whose *top* pair is the board's own pair: hero's real edge is
        # only the lower pair, so grade it as a bluff-catcher (mirrors the
        # paired-board one-pair demotion) rather than `strong_made` value. This
        # stops the overbet/value layers from firing a 150% pot bet with a hand
        # that loses to the whole continuing range. See
        # _two_pair_topped_by_board_pair.
        made_tier = 'weak_made'
        nut_status = NUT_BLUFF_CATCHER

    hand_class = simplify_hand_class(made_tier, draw_modifier)

    return HandClassification(
        made_tier=made_tier,
        draw_modifier=draw_modifier,
        hand_class=hand_class,
        nut_status=nut_status,
        danger_flags=danger_flags,
    )


def classify_hand(
    hole_cards: List[str],
    community_cards: List[str],
) -> Tuple[str, str]:
    """Classify a hand into (made_tier, draw_modifier).

    Thin wrapper around `classify_hand_full` for legacy callers; the
    returned `made_tier` is the *downgraded* tier (so existing
    consumers like `simplify_hand_class` and the postflop strategy
    table see the corrected value without code changes).

    Args:
        hole_cards: Two card strings like ['Ah', 'Kd']
        community_cards: Three to five card strings like ['Ks', '7d', '2c']

    Returns:
        Tuple of (made_tier, draw_modifier) where:
        - made_tier: 'nuts', 'strong_made', 'medium_made', 'weak_made', 'air'
        - draw_modifier: 'strong_draw', 'weak_draw', 'backdoor', 'no_draw'
    """
    classification = classify_hand_full(hole_cards, community_cards)
    return classification.made_tier, classification.draw_modifier


def simplify_hand_class(made_tier: str, draw_modifier: str) -> str:
    """Map (made_tier, draw_modifier) to one of 6 simplified classes.

    Returns one of: 'nuts', 'strong_made', 'medium_made', 'weak_made',
    'air_strong_draw', 'air_no_draw'.
    """
    if made_tier == 'nuts':
        return 'nuts'
    if made_tier == 'strong_made' and draw_modifier == 'strong_draw':
        return 'nuts'
    if made_tier == 'strong_made':
        return 'strong_made'
    if made_tier == 'medium_made' and draw_modifier == 'strong_draw':
        return 'strong_made'
    if made_tier == 'medium_made':
        return 'medium_made'
    if made_tier == 'weak_made' and draw_modifier == 'strong_draw':
        return 'medium_made'
    if made_tier == 'weak_made':
        return 'weak_made'
    if made_tier == 'air' and draw_modifier == 'strong_draw':
        return 'air_strong_draw'
    return 'air_no_draw'


# Deterministic equity proxy (made_tier, draw_modifier) -> [0,1].
# Calibrated so each tier lands where the MC-vs-random equity put it AND keeps
# weak/medium made hands above the bluff-catch threshold (the MC undervalue here
# made CaseBotV2 fold to air-barrels — punisher edge dropped +457->+209).
_MADE_TIER_BASE = {
    'nuts': 0.87,
    'strong_made': 0.72,
    'medium_made': 0.58,
    'weak_made': 0.46,
    'air': 0.13,
}
_DRAW_BUMP = {'strong_draw': 0.20, 'weak_draw': 0.08, 'backdoor': 0.02, 'no_draw': 0.0}


def equity_from_made_tier(made_tier: str, draw_modifier: str, num_opponents: int = 1) -> float:
    """A deterministic stand-in for `calculate_quick_equity`'s postflop Monte
    Carlo, mapping the made-hand classification to an equity in the same
    `_equity_category` buckets. Zero MC → no per-decision cost.

    NOT WIRED — kept only as a documented utility. Tried as a rule-bot MC
    replacement (7x faster): it matched CaseBotV2's results vs the calling
    opponents (jeff/punisher) but is NOT decision-equivalent overall — it made
    CaseBotV2 LOSE to maniacs (−96 bb/100 across 6 seeds, where the MC had it
    winning ~+150), because a categorical map can't reproduce the MC's per-board
    equity nuance and the calibration that fixed the callers over-valued hands
    into the maniac's fighting range. The rule bots stayed on the 64-sim MC.
    Usable where coarse equity is genuinely sufficient and adversarial
    value-betting isn't on the line.
    """
    base = _MADE_TIER_BASE.get(made_tier, 0.40)
    bump = _DRAW_BUMP.get(draw_modifier, 0.0)
    if made_tier in ('air', 'weak_made'):
        base += bump  # draws matter most when you're otherwise behind
    elif made_tier == 'medium_made':
        base += 0.5 * bump
    base -= 0.02 * max(0, (num_opponents or 1) - 1)
    return max(0.02, min(0.97, base))
