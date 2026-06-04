"""Scene-0, played AT THE TABLE — the rigged-deck hand script + deck builder.

The career onboarding is no longer a lobby pop-up; it's a short, rigged session
dealt on the real felt (`docs/plans/CASH_MODE_CAREER_PROGRESSION.md` → "The
Circuit"). The mentor (Sal) narrates in chat, the fish (Larry) plays the soft
spot, and a handful of teaching hands are *pre-stacked* so the lesson always
appears — using the state machine's one-shot `provide_hand_deck` seam.

This module is the data + pure deck-building. The game-handler hook
(`flask_app/handlers/game_handler.py`) drives it: at each Scene-0 hand boundary
it asks for the next hand's rigged deck (or None for a normal hand) and feeds it
to the state machine before the deal.

Deal order is sequential pairs by player index (`poker_game.deal_hole_cards`),
then flop/turn/river off the top — so a deck of
`[seat0 hole, seat1 hole, …, flop, turn, river, filler…]` pins every card.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Roles the script assigns cards to. The hook maps live seats → roles (the human
# is HERO; the Sal/Larry personas are MENTOR/FISH).
ROLE_HERO = "hero"
ROLE_MENTOR = "mentor"
ROLE_FISH = "fish"


@dataclass(frozen=True)
class Scene0Hand:
    """One hand in the Scene-0 script.

    `holes` maps role → 2 short-string cards; `board` is 5 short-string cards
    (flop+turn+river). A hand with `rigged=False` deals normally (no deck
    override) — used for the opening "just poker" hand and quiet fillers.

    Teaching hands carry the lesson metadata: `lesson` (key), `correct_action`
    (what the hero should do — judged from their actual table action), and Sal's
    lines (`sal_setup` before/while it develops, `sal_pass`/`sal_fail` after).
    """

    rigged: bool = False
    holes: Dict[str, List[str]] = field(default_factory=dict)
    board: List[str] = field(default_factory=list)
    lesson: Optional[str] = None
    correct_action: Optional[str] = None
    # How the finished hand is judged from end state: 'not_folded' (the hero
    # should stay in — value / bluff-catch) or 'folded' (the hero should lay it
    # down — discipline). Only meaningful on teaching hands (those with a lesson).
    pass_when: str = "not_folded"
    sal_setup: str = ""
    sal_pass: str = ""
    sal_fail: str = ""
    # Per-phase scripted-action intents for the AI cast, so the lesson is
    # reliable rather than hoping the bots cooperate. Phase name → intent;
    # missing phase = let the bot decide. Intents: 'fold' (get out of the way),
    # 'limp'/'stay' (call/check in cheap), 'passive' (check, else call — sticky),
    # 'bluff' (bet when checked to; give up when bet into). Resolved against the
    # live legal actions by `resolve_scripted_action`.
    fish_plan: Dict[str, str] = field(default_factory=dict)
    mentor_plan: Dict[str, str] = field(default_factory=dict)
    # The fish's own table chatter — the comedy that sells him as a fish (and
    # quietly seeds "wait… are these people literally fish?"). `fish_setup` fires
    # as the hand opens; `fish_react` after it's judged. Loud, clueless, *blub*.
    # Larry never figures out he's the mark. Empty = silent (real-game texture).
    fish_setup: str = ""
    fish_react: str = ""
    # Per-STREET fish chatter, keyed by phase name ('FLOP'/'TURN'/'RIVER'). Fires
    # once when that community card street is dealt — so the fish can react to what
    # he actually CATCHES rather than telegraphing strength at hand-open. The
    # discipline hand uses it: Larry is a clueless limp preflop (junk J9o) and only
    # comes ALIVE on the flop, the moment he makes the nut straight (mirroring Chan,
    # who was quiet then sprang the trap). Empty = no mid-hand fish line.
    fish_streets: Dict[str, str] = field(default_factory=dict)
    # The FINALE: lift the no-bust guard so Sal can drain the fish to zero
    # (bets uncapped, 'passive' calls the all-in off, 'shove' jams). Sal is seeded
    # to cover, so the loser's chips just transfer — no minting.
    bust_ok: bool = False


# Pot-fraction for each bet-size tag (scripted aggression). The fish's tells are
# loud on purpose — over-bets read clearly as "he's barreling".
SIZE_FRAC: Dict[str, float] = {
    "half": 0.5,
    "twothirds": 0.66,
    "big": 0.85,
    "pot": 1.0,
    "overbet": 1.25,
}
# A scripted bet is also capped at this fraction of the actor's stack, so a fish
# barreling a bluff and getting called down can't bust himself (we can't top him
# up — that would mint chips). Lets Larry over-bet small pots while staying alive.
MAX_SCRIPTED_BET_STACK_FRAC = 0.5


def resolve_scripted_action(
    *,
    intent: str,
    valid_actions: List[str],
    cost_to_call: int,
    pot_total: int,
    stack: int,
    big_blind: int,
    size_frac: float = 0.7,
    allow_bust: bool = False,
) -> Optional[Dict]:
    """Turn a scripted intent into a concrete, legal action for the cast.

    Returns ``{'action', 'amount'}`` or None (no legal scripted move → caller
    falls back to the bot). Always legal: 'fold' downgrades to 'check' when free;
    'bluff'/'bet' downgrade to fold/check when bet into; 'passive' folds to an
    all-in rather than busting. Bet sizing is `size_frac` of the pot, capped at
    `MAX_SCRIPTED_BET_STACK_FRAC` of the stack (the no-bust guard).

    ``allow_bust`` (the Scene-0 FINALE) lifts the no-bust guard: bets aren't
    stack-capped, 'passive' CALLS an all-in instead of folding to it, and the
    'shove' intent jams the whole stack — so Sal can drain Larry to zero. Only set
    on a hand authored to bust the loser (Sal covers; chips just transfer).
    """
    va = set(valid_actions)
    facing_bet = cost_to_call > 0

    def _bet_amount() -> int:
        by_pot = round(size_frac * max(pot_total, big_blind))
        cap = stack if allow_bust else round(stack * MAX_SCRIPTED_BET_STACK_FRAC)
        return int(min(stack, max(big_blind * 2, min(by_pot, cap))))

    if intent == "fold":
        if facing_bet and "fold" in va:
            return {"action": "fold", "amount": 0}
        if "check" in va:
            return {"action": "check", "amount": 0}
        if "fold" in va:
            return {"action": "fold", "amount": 0}
        return None

    if intent in ("limp", "stay"):
        if facing_bet and "call" in va:
            return {"action": "call", "amount": 0}
        if "check" in va:
            return {"action": "check", "amount": 0}
        if "call" in va:
            return {"action": "call", "amount": 0}
        return None

    if intent == "passive":
        # Call along (sticky station). Normally folds to an all-in rather than
        # bust; on a bust_ok finale it calls the all-in off and busts. Note: when
        # the call would commit the whole stack the engine surfaces 'all_in' (not
        # 'call') as the only legal way to match — so calling off an all-in means
        # taking the 'all_in' action, not 'call'.
        if facing_bet and cost_to_call >= stack:
            if not allow_bust:
                if "fold" in va:
                    return {"action": "fold", "amount": 0}
            else:
                if "all_in" in va:
                    return {"action": "all_in", "amount": 0}
                if "call" in va:
                    return {"action": "call", "amount": 0}
        if "check" in va:
            return {"action": "check", "amount": 0}
        if "call" in va:
            return {"action": "call", "amount": 0}
        return None

    if intent == "shove":
        # Jam the whole stack (finale). Prefer the explicit 'all_in' action: a
        # raise-TO of `stack` UNDER-shoves whenever the actor already has chips in
        # this street (raise amount is the raise-TO total, so it leaves the
        # already-posted bet behind — the fish keeps a sliver and never busts).
        # 'all_in' commits the whole stack regardless of the posted bet.
        if "all_in" in va:
            return {"action": "all_in", "amount": 0}
        if not facing_bet and "raise" in va:
            return {"action": "raise", "amount": int(stack)}  # legacy fallback
        if facing_bet and "call" in va:
            return {"action": "call", "amount": 0}
        if "check" in va:
            return {"action": "check", "amount": 0}
        return None

    if intent in ("bluff", "bet"):
        # Bet/barrel when checked to; give up the air when bet into.
        if not facing_bet and "raise" in va:
            return {"action": "raise", "amount": _bet_amount()}
        if intent == "bet" and facing_bet and "call" in va:
            # A value 'bet' that gets check-raised just calls along.
            return {"action": "call", "amount": 0}
        if "fold" in va:
            return {"action": "fold", "amount": 0}
        if "check" in va:
            return {"action": "check", "amount": 0}
        return None

    return None


# Sal's graduation beat — fired as a short SEQUENCE when the Scene-0 script ends.
# This is the emotional payoff the early build was missing: the one elegant
# mechanic (fish can't hear strategy talk — you could, from the first hand), the
# fish-name shed, and the vouch. Said light, never overplayed. See
# docs/plans/CASH_MODE_CAREER_PROGRESSION.md → "The Circuit".
SAL_GRADUATION_SEQUENCE = [
    # Bridge off the finale hand he just won — the showing-off "talk to you" beat.
    "Ha — pay that man his money. Kiddin', it's mine now. THAT's the whole lesson, "
    "kid: bet your big hands, bleed the fish slow, and they pay you every street. "
    "You were watchin' close. I could tell.",
    # The reveal: how Sal knew you weren't a fish. The bond and the lesson in one.
    "You ever notice none of 'em answer me, kid? All night I been talkin' — "
    "strategy, tells, the whole bit. Larry never heard a word. But you? You heard "
    "me from the very first hand. That's how I knew.",
    # The shed: a fish can't get vouched — so somewhere back there you stopped
    # being one. (The fish-name comes off here; the lobby starts calling you Jeff.)
    "See, a fish can't get vouched — that's the one rule of this place. Lucky for "
    "you, somewhere around that last hand, you stopped bein' one. So you can drop "
    "the tourist name. You're just you now.",
    # The vouch + the endgame foreshadow (Sal's whole deal, said offhand).
    "I'm gonna put your name in somewhere real — tell 'em Sal sent ya, don't make "
    "me look bad. Me, I'm not on the circuit anymore. I just come out here to feed "
    "the fish. Maybe someday you'll get it. Now go on — your coffee's gettin' cold.",
]
# Back-compat single line (older callers / tests reference SAL_GRADUATION).
SAL_GRADUATION = SAL_GRADUATION_SEQUENCE[0]


def build_hand_deck(hand: Scene0Hand, *, num_players: int, role_seats: Dict[str, int]) -> tuple:
    """Build a pre-stacked 52-card deck (tuple of Cards) for a rigged hand.

    `role_seats` maps each role to its seat index in the game's player list. Each
    seat's hole cards land at `deck[2*seat : 2*seat+2]`; the board follows all
    hole cards; the rest is filler (a shuffled standard deck minus placed cards,
    deterministic given no seed needed — order of filler is irrelevant to the
    scripted streets). Raises if the script references a seat out of range or a
    card is placed twice.
    """
    from core.card import Card
    from poker.poker_game import create_deck

    def _cards(shorts: List[str]) -> List:
        return [Card.from_short(s) for s in shorts]

    def _key(c) -> tuple:
        return (c.rank, c.suit)

    # seat index -> the 2 hole Cards for whoever sits there
    seat_holes: Dict[int, List] = {}
    for role, seat in role_seats.items():
        if role not in hand.holes:
            continue
        if not (0 <= seat < num_players):
            raise ValueError(f"scene0: role {role!r} seat {seat} out of range (n={num_players})")
        seat_holes[seat] = _cards(hand.holes[role])

    board = _cards(hand.board)
    if len(board) != 5:
        raise ValueError(f"scene0: rigged hand needs a 5-card board, got {len(board)}")

    placed: List = []
    for seat in range(num_players):
        placed.extend(seat_holes.get(seat, []))  # filled below if a seat has no role
    placed.extend(board)

    # Any seat without a scripted hole gets filler pairs (so the deck still deals
    # cleanly even if an extra AI is somehow at the table).
    placed_keys = [_key(c) for c in placed]
    if len(set(placed_keys)) != len(placed_keys):
        raise ValueError("scene0: a card was placed more than once in the rigged hand")

    pool = [c for c in create_deck(shuffled=True, random_seed=0) if _key(c) not in set(placed_keys)]
    pool_iter = iter(pool)

    ordered: List = []
    for seat in range(num_players):
        if seat in seat_holes:
            ordered.extend(seat_holes[seat])
        else:
            ordered.extend([next(pool_iter), next(pool_iter)])
    ordered.extend(board)
    ordered.extend(pool_iter)  # remaining filler
    return tuple(ordered)


# --- The Scene-0 script ------------------------------------------------------
# ~10 hands: hand 1 is just poker; three teaching hands (value / bluff-catch /
# discipline) are seeded among quiet rigged fillers so it reads like a real soft
# game, not a quiz. Every rigged hand keeps pots small (Sal junk-folds, scripted
# bets are stack-capped) so nobody busts. Sal narrates the lessons in chat.

# Junk hole cards for whoever should fold out of the way on a teaching hand.
_SAL_JUNK = ["7s", "2c"]


def _filler(
    hero: List[str],
    board: List[str],
    *,
    fish: List[str],
    sal: List[str] = None,
    fish_setup: str = "",
) -> Scene0Hand:
    """A quiet, no-lesson rigged hand: Sal folds, Larry plays passive, small pot.

    `fish_setup` lets a filler carry a throwaway *blub* one-liner so the table
    breathes between lessons instead of going dead silent.
    """
    return Scene0Hand(
        rigged=True,
        holes={ROLE_HERO: hero, ROLE_FISH: fish, ROLE_MENTOR: sal or _SAL_JUNK},
        board=board,
        fish_plan={"PRE_FLOP": "limp", "FLOP": "passive", "TURN": "passive", "RIVER": "passive"},
        mentor_plan={"PRE_FLOP": "fold"},
        fish_setup=fish_setup,
    )


# VALUE — the slow-played set vs the calling station.
# Real-hand anchor: the Red Chip "$5/$10 set of sevens" teaching hand (K-7-2,
# flop a set, three streets of value off a station that can't fold top pair).
# See docs/plans/CASH_MODE_FAMOUS_HANDS_LIBRARY.md #4.
_VALUE = Scene0Hand(
    rigged=True,
    holes={
        ROLE_HERO: ["7s", "7h"],  # pocket sevens → flopped set of sevens
        ROLE_FISH: ["Kh", "Qd"],  # top pair (kings) — the station that pays off
        ROLE_MENTOR: ["Jc", "3d"],  # junk; Sal folds (own junk avoids the 7s/2c on this board)
    },
    board=["Ks", "7d", "2c", "9h", "4s"],  # 7 on the flop = hero's set; K = Larry's top pair
    lesson="value",
    correct_action="bet",
    pass_when="not_folded",
    # Sal can't see the hero's cards — he coaches the PRINCIPLE off Larry's
    # public behavior, never the hero's hand. The player reads their own set.
    # Thinking out loud about Larry (the strategy talk a fish glazes past) — not
    # yet coaching YOU. The player reads their own set and applies it.
    sal_setup=(
        "Funny thing about ol' Larry there — he'll call with a postcard, folds "
        "nothin'. No quit in the man. Way you beat a fella like that's simple: "
        "you flop somethin' good, you bet it — every street. No sense slow-playin' "
        "a man who'll pay you off anyhow."
    ),
    # Pass = the hero stayed in → showdown → Larry's weak pair is public.
    sal_pass="See that? Paid off with one pair — that's free money. That's why you bet your good hands at a station, not check 'em and pray.",
    sal_fail="A man who calls everything, you bet everything — don't go givin' the fish a free pass next time.",
    fish_setup="Ooh, a king! I like kings.\n*blub*\nYou comin' along, friend?",
    fish_react="Aw, ya got me. Great hand, buddy — I almost had ya! Deal again, deal again.",
    # Larry just calls the hero's value bets (and never bets himself).
    fish_plan={"PRE_FLOP": "limp", "FLOP": "passive", "TURN": "passive", "RIVER": "passive"},
    mentor_plan={"PRE_FLOP": "fold"},
)

# BLUFF-CATCH — the hero sits in the seat the legend misplayed.
# Real-hand anchor: Moneymaker vs Farha, 2003 WSOP Main Event (the "bluff of the
# century"). YOU are Farha — Q♠9♥, top pair — and Larry is Moneymaker barreling
# busted king-high air. Farha *folded the best hand*; you call. Board recast to a
# consistent suit set (Larry stays pure king-high — the public boards conflict;
# see docs/plans/CASH_MODE_FAMOUS_HANDS_LIBRARY.md #1).
_BLUFF_CATCH = Scene0Hand(
    rigged=True,
    holes={
        ROLE_HERO: ["Qs", "9h"],  # top pair (nines) — the hand Farha folded
        ROLE_FISH: ["Ks", "7h"],  # king-high, missed everything — pure air
        ROLE_MENTOR: _SAL_JUNK,
    },
    board=["9c", "2d", "6s", "8h", "3c"],  # 9-high; Larry's K7 makes nothing
    lesson="bluff_catch",
    correct_action="call",
    pass_when="not_folded",
    # Still musing aloud — but the first flicker that the right play is rare here
    # (a quiet nod toward the fact that YOU might be different, without saying so).
    sal_setup=(
        "Now watch Larry close this one. When he's got nothin' he can't help "
        "himself — fires and fires, tryin' to scare folks off the pot. Anybody "
        "with a piece worth callin' oughta just look him up. Most around here "
        "won't, though. Their loss."
    ),
    # Pass = hero called → showdown → Larry's air is public.
    sal_pass="What'd I tell ya — king-high nothin', three streets of it. You looked him up. Better men have folded that and kicked themselves all night.",
    # Fail = hero folded → no showdown → Sal speaks to the tendency, not the muck.
    sal_fail="He pushed ya right off it, kid. A fish that barrels like that is usually full of air — next time, you look him up. Remember the sting.",
    fish_setup="I'm feelin' lucky on this one, fellas!\n*blub*\nGonna bet big — scaaary, right?",
    fish_react="Aw, ya called?! I had nothin'! Heh — ya got me, buddy. Smart cookie.",
    # Larry barrels the bluff — turn + an over-bet river (stack-capped, no bust).
    fish_plan={
        "PRE_FLOP": "limp",
        "FLOP": "passive",
        "TURN": ("bluff", "twothirds"),
        "RIVER": ("bluff", "overbet"),
    },
    mentor_plan={"PRE_FLOP": "fold"},
)

# DISCIPLINE — the other seat the legend misplayed.
# Real-hand anchor: Chan vs Seidel, 1988 WSOP Main Event (the Rounders hand). YOU
# are Seidel — Q♣7♣, top pair queens — and Larry is Chan, the quiet fish who
# flopped the nut straight with J9 and suddenly comes alive. Seidel *couldn't get
# away from top pair*; you can. See docs/plans/CASH_MODE_FAMOUS_HANDS_LIBRARY.md #3.
_DISCIPLINE = Scene0Hand(
    rigged=True,
    holes={
        ROLE_HERO: ["Qc", "7c"],  # top pair (queens) — looks great, is dead
        ROLE_FISH: ["Jh", "9s"],  # flopped the nut straight (Q-J-T-9-8) — the trap
        ROLE_MENTOR: _SAL_JUNK,
    },
    board=["Qd", "8s", "Tc", "2s", "6h"],  # Q-8-T flop gives Larry 8-9-T-J-Q
    lesson="discipline",
    correct_action="fold",
    pass_when="folded",
    # The recognition surfaces: he notices YOU'VE been tracking the talk all night
    # (fish don't) — and for the first time addresses you directly as "kid".
    sal_setup=(
        "Huh. You're still trackin' all this, ain'tcha — most folks have glazed "
        "over by now. Alright, kid: careful this one. Even a fish catches a card. "
        "Larry's limp-called all night, so if he wakes up and bets BIG out of "
        "nowhere — that ain't a bluff. Don't pay him off just 'cause you got "
        "something."
    ),
    # Pass = hero folded → no showdown → Sal speaks to the tendency, not Larry's muck.
    sal_pass="Good lay-down. A quiet fella only comes alive like that with the goods. Knowin' when to fold is the whole job, kid — the greats lost titles forgettin' it.",
    # Fail = hero called → showdown → Larry's straight is public.
    sal_fail="Oof — he flopped the joint and trapped ya cold. When the quiet one suddenly bets the farm, believe him. Dodge that one next time.",
    # Preflop he's his usual clueless self — J-nine offsuit is nothing yet, and a
    # fish has no idea it's about to become everything. The tell comes WHEN he hits.
    fish_setup="Eh, jack-nine. I'll tag along, sure.\n*blub*\nWhy not, right?",
    # FLOP (Q-8-T) makes his nut straight — NOW he wakes up, loud and obvious. This
    # is the read Sal warned about: the quiet limper suddenly betting big = the goods.
    fish_streets={
        "FLOP": "Oh! Oh! NOW I like it!\n*blub blub*\nLookit them cards line up — I'm bettin' a LOT, fellas, a LOT!",
    },
    fish_react="Hee hee, I had the good cards that time! See? Even I get 'em. Don't feel bad, friend.",
    # Larry bets his straight for value; the hero should fold top pair.
    fish_plan={
        "PRE_FLOP": "limp",
        "FLOP": ("bet", "twothirds"),
        "TURN": ("bet", "big"),
        "RIVER": ("bet", "big"),
    },
    mentor_plan={"PRE_FLOP": "fold"},
)

# THE FINALE — Sal stacks Larry. Not a lesson the hero plays; a showcase the hero
# WATCHES. Sal calls his shot ("keep your chips in your pocket… watch me bleed him
# slow"), flops a set (a callback to the value hand he taught), raises a bit each
# street, and shoves the river to bust Larry — who, sticky as ever, calls it off.
# `bust_ok` lifts the no-bust guard; Sal is seeded to cover (see ensure_scene0_seeded).
_FINALE = Scene0Hand(
    rigged=True,
    bust_ok=True,
    holes={
        ROLE_MENTOR: ["7s", "7d"],  # Sal flops a set of sevens (the hidden monster)
        ROLE_FISH: ["Kh", "Qd"],  # Larry: top pair kings — can't lay it down
        ROLE_HERO: ["Jc", "3h"],  # junk; the hero sits this one out and watches
    },
    board=["Ks", "7h", "2c", "9d", "4c"],
    # No lesson/judging — it's Sal's hand. Sal limps in, then bets a little more
    # each street, and jams the river. Larry calls all the way (and calls it off).
    mentor_plan={
        "PRE_FLOP": "limp",
        "FLOP": ("bet", "half"),
        "TURN": ("bet", "twothirds"),
        "RIVER": ("shove", None),
    },
    fish_plan={"PRE_FLOP": "limp", "FLOP": "passive", "TURN": "passive", "RIVER": "passive"},
    sal_setup=(
        "Last one, kid — this one's on me. Keep your chips in your pocket and watch. "
        "See Larry's stack? I'm gonna raise him a little on every street, nice and "
        "slow, till there's nothin' left. He won't even feel it go."
    ),
    fish_setup="Ooh, I like this hand! Feelin' good, Sal, real good.\n*blub*",
    fish_react="Wh— hey, where'd all my chips go? Heh… you're somethin' else, Sal!",
)


SCENE0_SCRIPT: List[Scene0Hand] = [
    # 0 — just poker. Sal greets you and lets you settle in. The wrong-turn /
    # biscuits-and-gravy gag is carried by the table, never voiced by you.
    Scene0Hand(
        rigged=False,
        # Slow burn: early Sal is a chatty old-timer running his mouth at no one
        # in particular (the canon mechanic — fish tune it out, you don't). He
        # does NOT single you out or coach you yet; that recognition builds and
        # only pays off at graduation. "friend" now → "kid" once he's clocked you.
        sal_setup=(
            "Sit down, sit down — plenty of room. Don't mind me, friend; I just "
            "like to talk while the cards run. Been doin' it so long the regulars "
            "tune me right out. First hand's a freebie — just feel it out."
        ),
        fish_setup="Hiya, new fella!\n*blub*\nYou here for the game or the biscuits and gravy? Heh — everybody says the biscuits.",
    ),
    # 1 — quiet filler; Larry burbles, the table breathes.
    _filler(
        ["Jd", "4s"],
        ["Qh", "8c", "3d", "Ts", "5h"],
        fish=["Tc", "6d"],
        fish_setup="Are clubs higher than spades? I never can remember.\n*blub*",
    ),
    # 2 — VALUE.
    _VALUE,
    # 3 — quiet filler.
    _filler(["9h", "2d"], ["Ac", "Kd", "Th", "4h", "Jc"], fish=["8s", "5c"]),
    # 4 — BLUFF-CATCH.
    _BLUFF_CATCH,
    # 5 — quiet filler; a little Sal-and-Larry texture.
    _filler(
        ["Qs", "3h"],
        ["7d", "6c", "2s", "Th", "4d"],
        fish=["9d", "5h"],
        fish_setup="I love it here. The water's always so nice and warm.\n*blub*",
    ),
    # 6 — DISCIPLINE.
    _DISCIPLINE,
    # 7 — quiet filler.
    _filler(["8d", "3s"], ["Ah", "Qd", "9s", "5d", "Tc"], fish=["Js", "6h"]),
    # 8 — quiet filler.
    _filler(
        ["Kh", "5s"],
        ["Td", "8h", "6s", "4c", "Qc"],
        fish=["9c", "4h"],
        fish_setup="Wait, is this the good kind of hand? It's got a picture on it.\n*blub*",
    ),
    # 9 — THE FINALE: Sal stacks Larry. Graduation fires after.
    _FINALE,
]


def hand_for_index(idx: int) -> Optional[Scene0Hand]:
    """The script entry for a 0-based hand index, or None past the end."""
    if 0 <= idx < len(SCENE0_SCRIPT):
        return SCENE0_SCRIPT[idx]
    return None


def script_length() -> int:
    return len(SCENE0_SCRIPT)
