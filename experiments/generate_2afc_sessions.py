#!/usr/bin/env python3
"""Generate labeled 2AFC perceptibility sessions (backlog #12, Phase 5).

This is the SESSION-GENERATION half of the 2AFC perceptibility harness described
in ``docs/plans/PERCEPTIBILITY_CONDITIONING.md`` (Phase 5) and the research
``docs/vision/texas_hold_em_research_text_markdown.md`` §2.1. It produces
ground-truth-labeled poker sessions that a human rater (or, for the adaptation
arm, an automated KL check in ``score_2afc.py``) can judge:

  (a) archetype-ID   — N single-archetype sessions, the hidden archetype tagged.
  (b) tilt 2AFC      — matched maniac pairs at the SAME seed/cards: one with the
                       ``tilt_conditioning`` layer firing on an injected
                       ``bad_beat`` composure state, one calm/flat. The "is this
                       player on tilt?" forced choice (d-prime).
  (c) adaptation 2AFC — matched pairs at the same cards with the exploitation
                       (opponent-modeling) layer forced ON (``exploitation_strength
                       = 1.0``) vs OFF (``0.0`` == effective_bias 0). "In which
                       session did opponents adjust to you?"

It is a TOOL, not a unit test: it is a ``__main__`` CLI and is never collected by
pytest. It reuses the deterministic, LLM-free sim machinery in
``experiments.simulate_bb100`` (``make_controller`` / ``make_game_state`` /
``drive_hand``) with seeded RNG so paired sessions are reproducible and share
duplicate hands (à la duplicate bridge — cancels card luck, research §2.3).

**No production code is changed.** The tilt arm flips the in-process
``TILT_CONDITIONING_ENABLED`` flag for the duration of generation (it is OFF by
default in prod) and injects a ``ComposureState`` onto the controller's psychology
namespace — exactly the documented sim hooks, no strategy edits.

------------------------------------------------------------------------------
SPEECH CHANNEL CAVEAT (read before running a human study)
------------------------------------------------------------------------------
The sim path does NOT call the LLM, so the player-facing *speech* in these
sessions is the DETERMINISTIC intuition text the code picks (spoken reads +
narration-facts observations) — i.e. the cue the LLM would be asked to voice,
NOT the voiced line. For the archetype-ID and tilt forced-choice human studies
you will want LLM-voiced speech. See the README's "automatable vs needs-humans"
section and the USES_EMOTIONAL_NARRATION open question.

Usage::

    python -m experiments.generate_2afc_sessions --arm adaptation --sessions 4 \
        --hands 60 --out /tmp/2afc_adaptation.json
    python -m experiments.generate_2afc_sessions --arm tilt --sessions 4 --hands 40
    python -m experiments.generate_2afc_sessions --arm archetype --hands 40
    python -m experiments.generate_2afc_sessions --arm all --out /tmp/2afc_all.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add project root to path so this runs as a module or a script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from poker.card_utils import card_to_string  # noqa: E402
from poker.memory.cbet_detector import CbetDetector  # noqa: E402
from poker.memory.opponent_model import OpponentModelManager  # noqa: E402
from poker.psychology_model import ComposureState  # noqa: E402
from poker.poker_state_machine import PokerStateMachine  # noqa: E402
from poker.strategy.spoken_reads import (  # noqa: E402
    SpokenReadConfig,
    SpokenReadState,
    select_spoken_reads,
)
from poker.strategy.strategy_table import load_strategy_table  # noqa: E402

from experiments.simulate_bb100 import (  # noqa: E402
    ARCHETYPES,
    make_controller,
    make_game_state,
)
from experiments._hand_loop import drive_hand  # noqa: E402

# The closed archetype list the rater chooses from (research §2.1a: chance = 1/n).
# These are the player-recognizable poker archetypes — not the eval-only rule
# bots. Chance for the archetype-ID task is 1 / len(ARCHETYPE_ID_CHOICES).
ARCHETYPE_ID_CHOICES = ["Nit", "Rock", "TAG", "LAG", "Calling Station", "Maniac", "WeakFish"]

# The non-hero seats that fill out a 6-max table. A fixed, identical backdrop
# across paired sessions so the only difference between arm members is the
# manipulated variable (tilt on/off, adaptation on/off). This mixed-archetype
# field is the natural reading-environment for the archetype-ID and tilt arms.
DEFAULT_BACKDROP = ["Calling Station", "Calling Station", "Nit", "TAG", "LAG"]

# The adaptation arm needs a backdrop the exploitation layer can actually read.
# The personality-archetype 'Calling Station' measures VPIP ~0.36 in-sim — below
# the hyper_passive threshold (0.70) — so it does NOT trip the exploitation rule
# and ON==OFF (a real null, not a harness bug). The rule-bot stations are the
# exploit_bb100.py recipe: CallStation (always-call, VPIP~1.0 / AF~0) trips
# hyper_passive; FoldyBot folds tight to flop pressure (the fold_to_cbet signal).
ADAPTATION_BACKDROP = ["CallStation", "CallStation", "FoldyBot", "FoldyBot", "Nit"]


# ── Captured records ──────────────────────────────────────────────────────────


@dataclass
class ActionRecord:
    """One observed action by any player in a hand (player-facing view)."""

    hand_index: int
    street: str  # PRE_FLOP / FLOP / TURN / RIVER
    seat: str  # anonymized seat label ("Hero", "Seat 2", ...)
    is_hero: bool
    action: str
    raise_to: int
    pot_before: int
    board: List[str]  # community cards visible at action time
    # Deterministic intuition text the hero would voice about an opponent on this
    # decision (spoken-read / narration-facts cue, NOT an LLM line). Empty when
    # nothing matured. See module docstring caveat.
    hero_speech: str = ""


@dataclass
class Session:
    """One labeled session. ``label`` is the ground truth — the viewer hides it
    until a choice is recorded; ``score_2afc`` reads it for scoring."""

    session_id: str
    arm: str  # 'archetype' | 'tilt' | 'adaptation'
    pair_id: Optional[str]  # links the two members of a 2AFC pair (tilt/adaptation)
    seed: int
    hands: int
    seat_labels: Dict[str, str]  # real seat name -> anonymized label
    actions: List[ActionRecord] = field(default_factory=list)
    # Per-hand hero hole cards, WITHHELD from the player-facing view (kept here so
    # a debug/replay view can reveal them, but the viewer/scoring never shows them
    # before a choice). Keyed by hand index.
    hero_hole_cards: Dict[int, List[str]] = field(default_factory=dict)
    label: Dict[str, object] = field(default_factory=dict)
    # Hero action-distribution histogram by (street, facing) bucket — the
    # automatable adaptation-KL substrate. Filled for the adaptation arm.
    hero_action_dist: Dict[str, Dict[str, int]] = field(default_factory=dict)


# ── Hand driver with capture ──────────────────────────────────────────────────


def _seat_labels(hero_name: str, all_names: List[str]) -> Dict[str, str]:
    """Anonymize seat names so the rater can't read the archetype off the label."""
    labels: Dict[str, str] = {}
    n = 1
    for name in all_names:
        if name == hero_name:
            labels[name] = "Hero"
        else:
            n += 1
            labels[name] = f"Seat {n}"
    return labels


def _run_session(
    *,
    session_id: str,
    arm: str,
    pair_id: Optional[str],
    hero_archetype: str,
    backdrop: List[str],
    n_hands: int,
    base_seed: int,
    exploitation_strength: float = 1.0,
    inject_tilt: Optional[str] = None,
    label: Dict[str, object],
) -> Session:
    """Drive one full session and capture the player-facing view + labels.

    ``exploitation_strength`` 0.0 turns the opponent-modeling adaptation layer
    into a no-op (effective_bias 0) — the OFF control for arm (c). ``inject_tilt``
    is a ``ComposureState.pressure_source`` (e.g. ``'bad_beat'``) forced onto the
    hero's psychology so the ``tilt_conditioning`` layer fires (arm (b)); None
    leaves the hero calm/composed.
    """
    strategy_table = load_strategy_table()

    hero_name = hero_archetype if hero_archetype not in backdrop else f"{hero_archetype}_hero"
    # Build unique opponent seat names.
    opp_seats: List[str] = []
    counts: Dict[str, int] = {}
    for o in backdrop:
        counts[o] = counts.get(o, 0) + 1
        opp_seats.append(f"{o}{counts[o]:02d}" if backdrop.count(o) > 1 else o)
    if hero_name in opp_seats:
        hero_name = f"{hero_archetype}_hero"
    all_names = [hero_name] + opp_seats
    labels = _seat_labels(hero_name, all_names)

    session = Session(
        session_id=session_id,
        arm=arm,
        pair_id=pair_id,
        seed=base_seed,
        hands=n_hands,
        seat_labels=labels,
        label=label,
    )

    # One opponent-model manager across the whole session so reads mature.
    opponent_manager = OpponentModelManager()
    # Local spoken-read state (per-session anti-spam, mirrors the controller's).
    spoken_state = SpokenReadState()
    spoken_config = SpokenReadConfig()

    config_hero = ARCHETYPES[hero_archetype]
    opp_configs = [ARCHETYPES[o] for o in backdrop]

    for hand_num in range(n_hands):
        hand_seed = base_seed + hand_num
        dealer_idx = hand_num % len(all_names)
        random.seed(hand_seed)

        gs = make_game_state(
            player_names=all_names,
            big_blind=100,
            starting_stack=10000,
            dealer_idx=dealer_idx,
            seed=hand_seed,
        )
        sm = PokerStateMachine(gs)
        sm.current_hand_seed = hand_seed

        hero_ctrl = make_controller(
            hero_name, config_hero, strategy_table, sm, rng_seed=hand_seed
        )
        # Adaptation arm lever: 0.0 == exploitation layer no-op (effective_bias 0).
        hero_ctrl.exploitation_strength = exploitation_strength
        hero_ctrl.opponent_model_manager = opponent_manager
        # Tilt arm lever: the tilt_conditioning layer's _resolve_tilt_type
        # requires BOTH (1) an aggressive-tilt emotional_state AND (2) a known
        # composure pressure_source (the CAUSE). The sim bypasses the full
        # PsychologyPipeline, so get_emotional_shift() reads zone_effects.penalties
        # to derive the state — we inject a `tilted` penalty zone so the emotional
        # selector fires, plus the composure pressure_source the rule keys on. The
        # calm twin leaves both unset (get_emotional_shift -> 'composed').
        if inject_tilt:
            import types as _types

            hero_ctrl.psychology.composure_state = ComposureState(pressure_source=inject_tilt)
            # zone_effects.penalties -> get_emotional_shift maps 'tilted' to the
            # aggressive 'tilted' EmotionalShift (extreme severity). Mirrors the
            # tilt_conditioning_probe's "EXTREME forced bad_beat tilt" setup.
            hero_ctrl.psychology.zone_effects = _types.SimpleNamespace(
                penalties={"tilted": 0.9}
            )
        else:
            hero_ctrl.psychology.composure_state = ComposureState()

        controllers = [hero_ctrl]
        for i, (seat, cfg) in enumerate(zip(opp_seats, opp_configs)):
            controllers.append(
                make_controller(
                    seat, cfg, strategy_table, sm, rng_seed=hand_seed + 1_000_000 * (i + 1)
                )
            )

        opponent_manager.record_hand_dealt(
            observer=hero_name, opponents=opp_seats, hand_number=hand_num
        )

        # Capture hero hole cards (withheld from player-facing view).
        hero_player = next(p for p in gs.players if p.name == hero_name)
        try:
            session.hero_hole_cards[hand_num] = [card_to_string(c) for c in hero_player.cards]
        except Exception:
            session.hero_hole_cards[hand_num] = []

        _capture_hand(
            sm=sm,
            controllers=controllers,
            hero_name=hero_name,
            labels=labels,
            hand_num=hand_num,
            session=session,
            opponent_manager=opponent_manager,
            opp_seats=opp_seats,
            spoken_state_ref=[spoken_state],
            spoken_config=spoken_config,
        )

    return session


def _capture_hand(
    *,
    sm: PokerStateMachine,
    controllers,
    hero_name: str,
    labels: Dict[str, str],
    hand_num: int,
    session: Session,
    opponent_manager: OpponentModelManager,
    opp_seats: List[str],
    spoken_state_ref: List[SpokenReadState],
    spoken_config: SpokenReadConfig,
) -> None:
    """Drive one hand, capturing every action + the hero's spoken-read cue.

    Feeds non-hero actions into the opponent model using the SAME opportunity-
    normalized ``was_facing_bet`` + c-bet-detector bookkeeping that
    ``simulate_bb100.run_hand`` uses — otherwise the stations never read as
    exploitable and the exploitation (adaptation) layer stays a no-op (the
    documented gotcha: VPIP/fold-to-cbet are opportunity-based, not raw-count).
    """
    hero_ctrl = next(c for c in controllers if c.player_name == hero_name)
    cbet_detector = CbetDetector()
    pre_fold_snapshot: List[str] = []

    def _on_decision(current_player, controller, action, raise_to, phase_name, gs, sim_street, decision):
        nonlocal pre_fold_snapshot
        board = []
        try:
            board = [card_to_string(c) for c in gs.community_cards]
        except Exception:
            board = []

        hero_speech = ""
        if current_player.name == hero_name:
            active_opps = [
                p.name for p in gs.players
                if not getattr(p, "is_folded", False) and p.name != hero_name
            ]
            try:
                obs, new_state, reads = select_spoken_reads(
                    observer_name=hero_name,
                    active_opponents=active_opps,
                    facing_opponent=None,
                    opponent_model_manager=opponent_manager,
                    state=spoken_state_ref[0],
                    config=spoken_config,
                )
                spoken_state_ref[0] = new_state
                if obs:
                    hero_speech = obs[0][1]
            except Exception:
                pass

            # Adaptation-KL substrate: bucket hero actions by (street, facing).
            facing = "facing_bet" if (raise_to == 0 and action in ("call", "fold")) else "open"
            bucket = f"{phase_name}:{facing}"
            dist = session.hero_action_dist.setdefault(bucket, {})
            dist[action] = dist.get(action, 0) + 1

        session.actions.append(
            ActionRecord(
                hand_index=hand_num,
                street=phase_name,
                seat=labels.get(current_player.name, current_player.name),
                is_hero=(current_player.name == hero_name),
                action=action,
                raise_to=int(raise_to or 0),
                pot_before=int(getattr(gs, "pot", {}).get("total", 0)) if hasattr(gs, "pot") else 0,
                board=board,
                hero_speech=hero_speech,
            )
        )

        # Snapshot active players BEFORE play_turn (CbetDetector needs the
        # pre-fold view to seed its facing-set on flop c-bets) — mirrors run_hand.
        pre_fold_snapshot = [
            p.name for p in gs.players if not getattr(p, "is_folded", False)
        ]

        # Compute was_facing_bet BEFORE the c-bet detector updates — mirror
        # AIMemoryManager.on_action / run_hand so the opportunity-normalized
        # VPIP/PFR/fold-to-cbet counters are correct (the exploitation signal).
        if phase_name in ("FLOP", "TURN", "RIVER"):
            recent = getattr(hero_ctrl, "_sim_recent_aggressor", None)
            was_facing_bet = (
                recent is not None and recent != current_player.name and sim_street == phase_name
            )
        elif phase_name == "PRE_FLOP":
            prior = cbet_detector.preflop_aggressor
            was_facing_bet = prior is not None and prior != current_player.name
        else:
            was_facing_bet = None

        if current_player.name != hero_name:
            opponent_manager.observe_action(
                observer=hero_name,
                opponent=current_player.name,
                action=action,
                phase=phase_name,
                is_voluntary=True,
                hand_number=hand_num,
                was_facing_bet=was_facing_bet,
            )

    def _post_action(current_player, action, raise_to, phase_name, gs, new_gs):
        # Drive the c-bet detector + apply fold_to_cbet observations to the hero's
        # opponent model — mirrors run_hand (this is the c-bet exploit signal).
        cbet_responses = cbet_detector.record_action(
            player_name=current_player.name,
            action=action,
            phase=phase_name,
            active_players=pre_fold_snapshot,
        )
        for opp_name, folded in cbet_responses:
            if opp_name == hero_name:
                continue
            model = opponent_manager.get_model(hero_name, opp_name)
            model.tendencies.update_fold_to_cbet(folded)

    drive_hand(
        sm,
        controllers,
        hero_name=hero_name,
        hero_controller=hero_ctrl,
        on_decision=_on_decision,
        post_action=_post_action,
    )


# ── Arm builders ───────────────────────────────────────────────────────────────


def build_archetype_arm(n_sessions: int, n_hands: int, base_seed: int) -> List[Session]:
    """Arm (a): N single-archetype sessions, ground-truth archetype tagged.

    Cycles through ARCHETYPE_ID_CHOICES so the rater sees a spread. Chance accuracy
    is 1 / len(ARCHETYPE_ID_CHOICES).
    """
    sessions: List[Session] = []
    for i in range(n_sessions):
        archetype = ARCHETYPE_ID_CHOICES[i % len(ARCHETYPE_ID_CHOICES)]
        seed = base_seed + i * 10_000
        sessions.append(
            _run_session(
                session_id=f"arch_{i:03d}",
                arm="archetype",
                pair_id=None,
                hero_archetype=archetype,
                backdrop=DEFAULT_BACKDROP,
                n_hands=n_hands,
                base_seed=seed,
                label={
                    "true_archetype": archetype,
                    "choices": ARCHETYPE_ID_CHOICES,
                    "chance": 1.0 / len(ARCHETYPE_ID_CHOICES),
                },
            )
        )
    return sessions


def build_tilt_arm(
    n_pairs: int, n_hands: int, base_seed: int, tilt_type: str = "bad_beat"
) -> List[Session]:
    """Arm (b): matched maniac pairs at the SAME seed/cards — one tilted (injected
    composure ``pressure_source`` so the tilt_conditioning layer fires), one calm.

    Flips ``TILT_CONDITIONING_ENABLED`` ON for the duration of generation (it is
    OFF in prod). Only the maniac is opted into the layer (Phase 3), so the maniac
    is the hero here.
    """
    import os
    from unittest import mock

    sessions: List[Session] = []
    # Enable the layer in-process only for generation. feature_flags.resolve()
    # reads an env var of the same name above the per-env default, so a scoped env
    # override is the documented, non-invasive toggle. patch.dict sets AND
    # auto-restores with NO raw `os.environ.get(<flag>)` — the registry stays the
    # only flag READER (see test_flags_are_only_read_through_the_registry).
    with mock.patch.dict(os.environ, {"TILT_CONDITIONING_ENABLED": "1"}):
        for i in range(n_pairs):
            seed = base_seed + i * 10_000
            pair_id = f"tilt_pair_{i:03d}"
            # Tilted member.
            sessions.append(
                _run_session(
                    session_id=f"{pair_id}_A",
                    arm="tilt",
                    pair_id=pair_id,
                    hero_archetype="Maniac",
                    backdrop=DEFAULT_BACKDROP,
                    n_hands=n_hands,
                    base_seed=seed,
                    inject_tilt=tilt_type,
                    label={"is_tilted": True, "tilt_type": tilt_type, "pair": pair_id},
                )
            )
            # Calm member — SAME seed/cards (duplicate hands).
            sessions.append(
                _run_session(
                    session_id=f"{pair_id}_B",
                    arm="tilt",
                    pair_id=pair_id,
                    hero_archetype="Maniac",
                    backdrop=DEFAULT_BACKDROP,
                    n_hands=n_hands,
                    base_seed=seed,
                    inject_tilt=None,
                    label={"is_tilted": False, "tilt_type": None, "pair": pair_id},
                )
            )
    return sessions


def build_adaptation_arm(
    n_pairs: int, n_hands: int, base_seed: int, hero_archetype: str = "TAG"
) -> List[Session]:
    """Arm (c): matched pairs at the same cards with the exploitation
    (opponent-modeling) adaptation layer ON (strength 1.0) vs OFF (0.0).

    Hero is a non-Baseline archetype (Baseline no-ops the layer). The backdrop
    has exploitable stations so the ON arm has something to adapt to.
    """
    sessions: List[Session] = []
    for i in range(n_pairs):
        seed = base_seed + i * 10_000
        pair_id = f"adapt_pair_{i:03d}"
        # ON member.
        sessions.append(
            _run_session(
                session_id=f"{pair_id}_ON",
                arm="adaptation",
                pair_id=pair_id,
                hero_archetype=hero_archetype,
                backdrop=ADAPTATION_BACKDROP,
                n_hands=n_hands,
                base_seed=seed,
                exploitation_strength=1.0,
                label={"adaptation_on": True, "pair": pair_id, "hero": hero_archetype},
            )
        )
        # OFF member — SAME seed/cards.
        sessions.append(
            _run_session(
                session_id=f"{pair_id}_OFF",
                arm="adaptation",
                pair_id=pair_id,
                hero_archetype=hero_archetype,
                backdrop=ADAPTATION_BACKDROP,
                n_hands=n_hands,
                base_seed=seed,
                exploitation_strength=0.0,
                label={"adaptation_on": False, "pair": pair_id, "hero": hero_archetype},
            )
        )
    return sessions


# ── Serialization ──────────────────────────────────────────────────────────────


def _session_to_dict(s: Session) -> dict:
    d = asdict(s)
    # JSON object keys must be strings.
    d["hero_hole_cards"] = {str(k): v for k, v in s.hero_hole_cards.items()}
    return d


def player_facing_view(s: Session) -> dict:
    """The view the viewer shows BEFORE a choice — labels + hole cards withheld."""
    d = _session_to_dict(s)
    d.pop("label", None)
    d.pop("hero_hole_cards", None)
    d.pop("hero_action_dist", None)
    return d


def write_sessions(sessions: List[Session], out_path: Path) -> None:
    payload = {
        "version": 1,
        "n_sessions": len(sessions),
        "sessions": [_session_to_dict(s) for s in sessions],
    }
    out_path.write_text(json.dumps(payload, indent=2))


# ── CLI ────────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Generate labeled 2AFC perceptibility sessions.")
    ap.add_argument(
        "--arm",
        choices=["archetype", "tilt", "adaptation", "all"],
        default="adaptation",
        help="Which detection arm to generate.",
    )
    ap.add_argument("--sessions", type=int, default=4, help="Sessions (arch) or pairs (tilt/adapt).")
    ap.add_argument("--hands", type=int, default=40, help="Hands per session.")
    ap.add_argument("--seed", type=int, default=42, help="Base RNG seed.")
    ap.add_argument("--out", type=str, default=None, help="Output JSON path.")
    ap.add_argument(
        "--adapt-hero", type=str, default="TAG", help="Hero archetype for the adaptation arm."
    )
    args = ap.parse_args(argv)

    sessions: List[Session] = []
    if args.arm in ("archetype", "all"):
        sessions += build_archetype_arm(args.sessions, args.hands, args.seed)
    if args.arm in ("tilt", "all"):
        sessions += build_tilt_arm(args.sessions, args.hands, args.seed + 100_000)
    if args.arm in ("adaptation", "all"):
        sessions += build_adaptation_arm(
            args.sessions, args.hands, args.seed + 200_000, hero_archetype=args.adapt_hero
        )

    out_path = Path(args.out) if args.out else Path(f"/tmp/2afc_{args.arm}.json")
    write_sessions(sessions, out_path)
    print(f"Wrote {len(sessions)} sessions -> {out_path}", file=sys.stderr)
    # Quick summary to stdout.
    for s in sessions:
        print(f"  {s.session_id:18s} arm={s.arm:11s} hands={s.hands} actions={len(s.actions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
