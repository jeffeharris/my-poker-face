"""Tourist factory — generate synthetic, ephemeral fish personalities for casinos.

Each call produces one `TouristProfile`: a synthetic personality_id, a
display name pulled from a per-template name pool, and a full
personality_dict that mirrors the existing `archetype: 'fish'` JSON
structure. Tourists exist only for the lifetime of one casino table; the
bundle is stored inline in the seat dict so controller construction can
read it without a DB lookup.

The factory replaces the persistent-fish model that required
`ai_bankroll_state` rows in the sandbox before any fish could be
considered for casino seating — see CASH_MODE_EPHEMERAL_TOURISTS.md for
the cold-start chicken-and-egg this fixes.

Each tourist carries one designated **leak** drawn from its template's
candidate pool. Two tourists from the same template at different casinos
can have different leaks, so grinders can't memorize `template → leak` —
they have to actually read the hands.

No LLM calls. No DB writes outside cash_tables.seats_json (and the
casino_seat_seed ledger row written by casino_provisioning).
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List

from poker.rule_strategies import FishLeak


@dataclass(frozen=True)
class TouristTemplate:
    """One caricature archetype: voice + play parameters + leak pool.

    Anchors mirror the existing fish personalities in personalities.json
    (see Vacation Greg, Bachelorette Brenda, etc.). The fish fingerprint
    is enforced by a unit test in test_tourist_factory.py — every
    template must satisfy the loose-passive-tilted-rattle-able pattern.
    """
    key: str                            # stable identifier (e.g. "vacation_dad")
    play_style: str                     # narrative string used by chat/voice prompts
    default_confidence: str             # free-form ("cheerful", "supremely overconfident")
    default_attitude: str               # free-form ("oblivious", "patronizing")
    anchors: Dict[str, float]           # full 9-field anchor block, fish fingerprint enforced
    verbal_tics: List[str]              # catchphrases / what they say at the table
    physical_tics: List[str]            # actions / mannerisms
    name_pool: List[str]                # first names that fit the archetype
    candidate_leaks: List[FishLeak]     # leak pool — factory picks one per spawn
    nickname_suffix: str = ""           # optional " (bachelorette)" / " (birthday)" tag


# --- Templates ---------------------------------------------------------
# The first four are lifted from the existing fish personalities in
# poker/personalities.json (Vacation Greg, Bachelorette Brenda, Cruise
# Carl, Birthday Bobby). The next four are new caricatures that fit the
# same casino-tourist feel.

TEMPLATE_VACATION_DAD = TouristTemplate(
    key="vacation_dad",
    play_style="loose-passive tourist; calls everything, never reads the board, here for fun",
    default_confidence="cheerful",
    default_attitude="oblivious",
    anchors={
        "baseline_aggression": 0.15, "baseline_looseness": 0.85,
        "ego": 0.2, "poise": 0.15, "expressiveness": 0.8,
        "risk_identity": 0.6, "adaptation_bias": 0.0,
        "baseline_energy": 0.7, "recovery_rate": 0.0,
    },
    verbal_tics=[
        "'Card games, baby! Wooooo!'",
        "'I think a flush beats a straight, right?'",
        "'Honey said I could lose $300 tonight, so I'm just getting started.'",
        "'One more hand, I feel it!'",
    ],
    physical_tics=[
        "*sips a frozen drink with a tiny umbrella*",
        "*adjusts Hawaiian shirt*",
        "*counts chips three times incorrectly*",
    ],
    name_pool=["Greg", "Dave", "Doug", "Rick", "Steve", "Mike", "Jeff", "Brad",
               "Chad", "Wayne", "Randy", "Kurt"],
    candidate_leaks=[
        FishLeak.CALLS_DOWN_TOP_PAIR,
        FishLeak.CHASES_ANY_DRAW,
        FishLeak.LIMPS_EVERY_HAND,
        FishLeak.OVERVALUES_FACE_CARDS,
    ],
)

TEMPLATE_BACHELORETTE = TouristTemplate(
    key="bachelorette",
    play_style="tipsy-aggressive on the bluffs, calling-station when sober — neither sustained for long",
    default_confidence="giggly",
    default_attitude="delighted",
    anchors={
        "baseline_aggression": 0.30, "baseline_looseness": 0.90,
        "ego": 0.4, "poise": 0.1, "expressiveness": 0.95,
        "risk_identity": 0.7, "adaptation_bias": 0.0,
        "baseline_energy": 0.85, "recovery_rate": 0.0,
    },
    verbal_tics=[
        "'WAIT — is it my turn?'",
        "'My girls said no more shots but YOLO.'",
        "'I have a feeling about this one!'",
        "'Sorry sorry sorry — call!'",
    ],
    physical_tics=[
        "*sash reading BRIDE TRIBE slipping off shoulder*",
        "*looks back at her bridesmaids and shrieks*",
        "*nearly knocks over chip stack*",
    ],
    name_pool=["Brenda", "Tiffany", "Ashley", "Brittany", "Megan", "Courtney",
               "Lauren", "Stacy", "Jenna", "Caitlin"],
    candidate_leaks=[
        FishLeak.CHASES_ANY_DRAW,
        FishLeak.LIMPS_EVERY_HAND,
        FishLeak.CALLS_RIVER_LIGHT,
        FishLeak.SPITE_RAISES_WHEN_LOSING,
    ],
    nickname_suffix=" (bachelorette)",
)

TEMPLATE_RETIRED_KNOW_IT_ALL = TouristTemplate(
    key="retired_know_it_all",
    play_style="retired insurance salesman convinced he's good at poker — calls down with second pair every time",
    default_confidence="supremely overconfident",
    default_attitude="patronizing",
    anchors={
        "baseline_aggression": 0.20, "baseline_looseness": 0.75,
        "ego": 0.85, "poise": 0.3, "expressiveness": 0.5,
        "risk_identity": 0.5, "adaptation_bias": 0.0,
        "baseline_energy": 0.5, "recovery_rate": 0.0,
    },
    verbal_tics=[
        "'In MY day, you'd never raise with that hand.'",
        "'I'll pay to see what you've got, son.'",
        "'They give you free drinks at sea, did you know that?'",
        "'I read a book on this once.'",
    ],
    physical_tics=[
        "*adjusts gold watch*",
        "*tells a story nobody asked for*",
        "*sniffs once, decisively, then calls*",
    ],
    name_pool=["Carl", "Frank", "Stan", "Vince", "Norm", "Harold", "Ernie",
               "Walt", "Lloyd", "Hank"],
    candidate_leaks=[
        FishLeak.DOESNT_BELIEVE_BIG_BETS,
        FishLeak.OVERVALUES_FACE_CARDS,
        FishLeak.POT_COMMITTED_EARLY,
        FishLeak.CALLS_DOWN_TOP_PAIR,
    ],
)

TEMPLATE_BIRTHDAY_KID = TouristTemplate(
    key="birthday_kid",
    play_style="it's his birthday, he's playing every hand, the math doesn't apply tonight",
    default_confidence="invincible",
    default_attitude="celebratory",
    anchors={
        "baseline_aggression": 0.25, "baseline_looseness": 0.95,
        "ego": 0.5, "poise": 0.1, "expressiveness": 0.85,
        "risk_identity": 0.8, "adaptation_bias": 0.0,
        "baseline_energy": 0.9, "recovery_rate": 0.0,
    },
    verbal_tics=[
        "'It's my BIRTHDAY, I gotta play!'",
        "'Birthday boy gets lucky, watch this.'",
        "'Dealer — when's my next free drink?'",
        "'I never fold on my birthday, it's tradition.'",
    ],
    physical_tics=[
        "*wears a plastic crown that keeps falling off*",
        "*high-fives nobody in particular*",
        "*announces 'BIRTHDAY POT' before every call*",
    ],
    name_pool=["Bobby", "Tommy", "Joey", "Kenny", "Danny", "Ricky", "Jimmy",
               "Mikey", "Sammy"],
    candidate_leaks=[
        FishLeak.LIMPS_EVERY_HAND,
        FishLeak.CALLS_RIVER_LIGHT,
        FishLeak.SPITE_RAISES_WHEN_LOSING,
        FishLeak.CHASES_ANY_DRAW,
    ],
    nickname_suffix=" (birthday)",
)

TEMPLATE_FINANCE_BRO = TouristTemplate(
    key="finance_bro",
    play_style="four whiskeys deep, keeps talking about pot odds while making bizarre folds and hero calls",
    default_confidence="performatively analytical",
    default_attitude="condescending-but-friendly",
    anchors={
        "baseline_aggression": 0.40, "baseline_looseness": 0.70,
        "ego": 0.7, "poise": 0.25, "expressiveness": 0.7,
        "risk_identity": 0.65, "adaptation_bias": 0.0,
        "baseline_energy": 0.75, "recovery_rate": 0.0,
    },
    verbal_tics=[
        "'I'm getting like 3-to-1 here, gotta call.'",
        "'Bro, I'm pot-committed, you know how it is.'",
        "'I literally read Sklansky.'",
        "'Variance, my dude. Variance.'",
    ],
    physical_tics=[
        "*adjusts Patagonia vest*",
        "*stares at chip stack doing visible mental math*",
        "*splashes the pot, knocks over his drink*",
    ],
    name_pool=["Chad", "Trent", "Brett", "Connor", "Tyler", "Hunter", "Garrett",
               "Brody"],
    candidate_leaks=[
        FishLeak.POT_COMMITTED_EARLY,
        FishLeak.DOESNT_BELIEVE_BIG_BETS,
        FishLeak.SPITE_RAISES_WHEN_LOSING,
        FishLeak.CALLS_DOWN_TOP_PAIR,
    ],
)

TEMPLATE_SUPERSTITIOUS_GRANDMA = TouristTemplate(
    key="superstitious_grandma",
    play_style="has a 'system' — plays hands based on the dealer's hat color and whether her drink has ice left",
    default_confidence="serene",
    default_attitude="grandmotherly",
    anchors={
        "baseline_aggression": 0.20, "baseline_looseness": 0.80,
        "ego": 0.3, "poise": 0.4, "expressiveness": 0.6,
        "risk_identity": 0.55, "adaptation_bias": 0.0,
        "baseline_energy": 0.55, "recovery_rate": 0.0,
    },
    verbal_tics=[
        "'The cards are warm tonight, dear.'",
        "'I always call with red queens. It's my late husband's birthday.'",
        "'I'm overdue.'",
        "'The dealer has nice eyes, I'll stay in.'",
    ],
    physical_tics=[
        "*kisses cards before looking at them*",
        "*rearranges chips into neat stacks of seven*",
        "*offers a butterscotch to the player on her left*",
    ],
    name_pool=["Mona", "Doris", "Ethel", "Mildred", "Phyllis", "Bernice",
               "Edna", "Gertrude"],
    candidate_leaks=[
        FishLeak.OVERVALUES_FACE_CARDS,
        FishLeak.CHASES_ANY_DRAW,
        FishLeak.CALLS_DOWN_TOP_PAIR,
        FishLeak.CALLS_RIVER_LIGHT,
    ],
)

TEMPLATE_SLOT_REFUGEE = TouristTemplate(
    key="slot_refugee",
    play_style="wandered over from the slots — treats every hand like a pull, no concept of relative position",
    default_confidence="zoned-out",
    default_attitude="agreeable",
    anchors={
        "baseline_aggression": 0.18, "baseline_looseness": 0.90,
        "ego": 0.2, "poise": 0.2, "expressiveness": 0.4,
        "risk_identity": 0.55, "adaptation_bias": 0.0,
        "baseline_energy": 0.4, "recovery_rate": 0.0,
    },
    verbal_tics=[
        "'Do I have to bet every time?'",
        "'How do you know if you won?'",
        "'I'll just put in whatever this stack is.'",
        "'Is there a free spin?'",
    ],
    physical_tics=[
        "*pulls an imaginary lever before each call*",
        "*stares blankly at the community cards*",
        "*sips a watery cocktail without expression*",
    ],
    name_pool=["Linda", "Karen", "Donna", "Cheryl", "Patty", "Sharon", "Joyce",
               "Marlene"],
    candidate_leaks=[
        FishLeak.CALLS_RIVER_LIGHT,
        FishLeak.LIMPS_EVERY_HAND,
        FishLeak.CHASES_ANY_DRAW,
    ],
)

TEMPLATE_GOLF_TRIP_DUDE = TouristTemplate(
    key="golf_trip_dude",
    play_style="taking a break from the round, treats poker like blackjack, only sees his own cards",
    default_confidence="loose and casual",
    default_attitude="upbeat",
    anchors={
        "baseline_aggression": 0.28, "baseline_looseness": 0.78,
        "ego": 0.45, "poise": 0.25, "expressiveness": 0.65,
        "risk_identity": 0.6, "adaptation_bias": 0.0,
        "baseline_energy": 0.7, "recovery_rate": 0.0,
    },
    verbal_tics=[
        "'Shot the front nine in 42, by the way.'",
        "'I always play position — that's golf talk.'",
        "'Beer me a call, dealer.'",
        "'The guys are gonna hear about this hand for years.'",
    ],
    physical_tics=[
        "*still wearing the visor*",
        "*air-swings a 7-iron between hands*",
        "*checks phone, photo of him at hole 14*",
    ],
    name_pool=["Brad", "Doug", "Kevin", "Scott", "Todd", "Greg", "Curt", "Jay"],
    candidate_leaks=[
        FishLeak.SPITE_RAISES_WHEN_LOSING,
        FishLeak.POT_COMMITTED_EARLY,
        FishLeak.DOESNT_BELIEVE_BIG_BETS,
        FishLeak.OVERVALUES_FACE_CARDS,
    ],
)

TOURIST_TEMPLATES: List[TouristTemplate] = [
    TEMPLATE_VACATION_DAD,
    TEMPLATE_BACHELORETTE,
    TEMPLATE_RETIRED_KNOW_IT_ALL,
    TEMPLATE_BIRTHDAY_KID,
    TEMPLATE_FINANCE_BRO,
    TEMPLATE_SUPERSTITIOUS_GRANDMA,
    TEMPLATE_SLOT_REFUGEE,
    TEMPLATE_GOLF_TRIP_DUDE,
]


# --- Profile dataclass + factory --------------------------------------


@dataclass(frozen=True)
class TouristProfile:
    """One generated tourist — everything a controller / UI needs.

    Lives only for the casino table's lifetime. The personality_dict is
    stashed inline in the seat (cash_tables.seats_json) so seat→
    personality lookups don't need to hit the personality_repo for
    ephemeral seats.
    """
    personality_id: str                 # "tourist-<uuid8>" — synthetic, table-scoped
    display_name: str                   # "Marge from Jersey" (with optional suffix)
    template_key: str                   # for capture/dossier aggregation
    personality_dict: Dict[str, Any]    # full personality config, ready to stash in seat


def _synth_pid() -> str:
    """Synthetic, table-scoped personality id. uuid4 to avoid collisions
    across spawns within a sandbox; 8 hex chars is plenty (2^32)."""
    return f"tourist-{uuid.uuid4().hex[:8]}"


def generate_tourist(rng: random.Random) -> TouristProfile:
    """Generate one tourist. Pure (modulo rng); no I/O.

    Picks a template uniformly at random, a name from that template's
    pool, and one leak from the template's candidate pool. Same template
    on two different calls can produce different (name, leak)
    combinations — that variance is the point.
    """
    template = rng.choice(TOURIST_TEMPLATES)
    first = rng.choice(template.name_pool)
    leak = rng.choice(template.candidate_leaks)
    display_name = f"{first}{template.nickname_suffix}".strip()
    pid = _synth_pid()
    personality_dict = {
        "name": display_name,
        "archetype": "fish",
        "ephemeral": True,
        "template_key": template.key,
        "play_style": template.play_style,
        "default_confidence": template.default_confidence,
        "default_attitude": template.default_attitude,
        "anchors": dict(template.anchors),
        "verbal_tics": list(template.verbal_tics),
        "physical_tics": list(template.physical_tics),
        "nickname": first,
        "bankroll_knobs": {
            "starting_bankroll": 0,             # ephemeral — no bankroll
            "bankroll_rate": 0,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$2",
        },
        "id": pid,
        "staker_profile": {"willing": False},
        "borrower_profile": {"willing": False},
        "rule_strategy": "fish",
        "fish_leak": leak.value,                # threaded into _strategy_fish via context
    }
    return TouristProfile(
        personality_id=pid,
        display_name=display_name,
        template_key=template.key,
        personality_dict=personality_dict,
    )


def generate_tourist_batch(
    rng: random.Random, count: int,
) -> List[TouristProfile]:
    """Generate `count` tourists with unique display names within the batch.

    Templates may repeat (only 8 of them, want flexibility). Names are
    sampled without replacement so two seats at one casino can't both
    be "Marge (bachelorette)" — that would break the rotating-disguise
    illusion. 500+ unique (name, suffix) combos across 8 templates vs
    CASINO_FISH_MAX = 4, so the rejection loop terminates fast.
    """
    used_names: set[str] = set()
    out: List[TouristProfile] = []
    # 20x attempts gives ample headroom; the actual collision rate at
    # 4 picks from 500+ combos is single-digit percent.
    max_attempts = max(20, count * 20)
    attempts = 0
    while len(out) < count and attempts < max_attempts:
        attempts += 1
        t = generate_tourist(rng)
        if t.display_name in used_names:
            continue
        used_names.add(t.display_name)
        out.append(t)
    return out
