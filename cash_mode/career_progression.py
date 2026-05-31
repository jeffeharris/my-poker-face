"""The Act-1 career-progression spine — the keyring, Scene 0, the first vouch.

`docs/plans/CASH_MODE_CAREER_PROGRESSION.md`. The lobby is a *keyring*, not a
menu: a brand-new player sees only one intimate, pinned tutorial table (Sal "The
Clock" Monroe + one fish + you). Cardrooms appear only once an AI has vouched
the player in. The world economy still runs across ALL tables — this layer is a
per-(sandbox, owner) visibility filter plus the scripted-graduation bookkeeping
(`poker/repositories/career_progress_repository.py`).

M1 (this module) ships the thinnest playable slice:
  - `ensure_scene0_seeded` — seed + pin the Scene-0 table for a new player and
    flip the keyring on (`career_active`).
  - `visible_tables` — the read-side filter the lobby applies.
  - `evaluate_first_vouch` — the crude, scripted graduation gate (min session
    hands + the player is up on the fish).
  - `fire_first_vouch` — reveal a random home-court cardroom, record the ticker
    beat, and mark Scene-0 complete.

The real relationship-driven `vouch_ready` model (respect-gated, likability-
driven, one-per-AI over the whole roster) is M2 and layers on top of the same
`CareerProgress.vouched_by` ledger this module already writes.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import List, Optional, Tuple

from cash_mode.tables import (
    TABLE_SEAT_COUNT,
    CashTableState,
    ai_slot,
    ai_slot_fish,
    open_slot,
)

logger = logging.getLogger(__name__)

# --- Authored Scene-0 cast (must match the personalities.json entries; both
# are seeded circulating=False so they never auto-populate the world) ---------
SAL_ID = "sal_moretti"
SAL_NAME = "Sal Monroe"
SCENE0_FISH_ID = "loose_larry"
SCENE0_FISH_NAME = "Loose Larry"

# --- The pinned Scene-0 table ------------------------------------------------
SCRIPTED_TABLE_TYPE = "scripted"
SCENE0_TABLE_ID = "cash-scene0-001"
SCENE0_TABLE_NAME = "The Clock's Table"
SCENE0_STAKE = "$2"
# Seats for the two authored players; the remaining 4 stay open so the player
# picks a chair (one human seat + headroom reads as a real, if quiet, table).
SCENE0_SAL_SEAT = 1
SCENE0_FISH_SEAT = 3

# --- The home court the first vouch reveals ----------------------------------
# Bias the FIRST vouch lateral: a reachable $2 cardroom (the "room where you come
# up"), not a climb. Vertical climbing falls out of where later vouchers sit (M2).
HOME_COURT_STAKE = "$2"

# --- Scripted graduation gate (M1: crude; tuned in playtest from M1 logging) --
# Min hands at the Scene-0 table before the vouch can fire — you can't graduate
# on hand one. Counted from the live session hand count.
MIN_VOUCH_HANDS = 10
# Chips the player must be net UP on the fish (cumulative cash_pair_stats PnL,
# owner-POV vs the fish). At $2 (bb=2, buy-in 80-200) this is ~20bb — a couple of
# real pots taken off the soft spot, not a single small one.
FISH_WIN_THRESHOLD = 40


# --- The Lucky Stack intake (the cold open) ----------------------------------
# A tourist handle the room slaps on a fresh face: an ALLITERATIVE poker/tourist
# adjective + the player's own first name ("Jeff" → "Juke-Joint Jeff"). Built
# deterministically (NOT by the LLM, which kept dropping the player's name) so it
# always keeps your name and always alliterates. Shed at the first vouch.
FISH_NAME_ADJECTIVES = {
    "a": ["Ace-High", "All-In", "Antsy", "Aw-Shucks"],
    "b": ["Big-Time", "Bluffin'", "Busted", "Bumblin'"],
    "c": ["Cooler", "Cocky", "Casino", "Clueless"],
    "d": ["Dapper", "Doomed", "Dealer's", "Dizzy"],
    "e": ["Easy", "Eager", "Empty-Pockets"],
    "f": ["Fishy", "Fresh", "Fumblin'", "Flush-Chasin'"],
    "g": ["Gamblin'", "Greenhorn", "Grinnin'"],
    "h": ["High-Roller", "Hapless", "Hot-Streak", "Hopeful"],
    "i": ["Itchy", "In-Too-Deep"],
    "j": ["Juke-Joint", "Jumpy", "Jackpot", "Jittery"],
    "k": ["Kooky", "Knockabout"],
    "l": ["Lucky", "Lowball", "Last-Call", "Loose"],
    "m": ["Moonshine", "Maverick", "Mumblin'"],
    "n": ["Nervous", "No-Limit", "Newbie"],
    "o": ["Out-of-Towner", "Overbet", "Oblivious"],
    "p": ["Pot-Stuck", "Penny-Ante", "Plucky"],
    "q": ["Quiet", "Quick-Fold"],
    "r": ["Riverboat", "Rookie", "Reckless", "Railbird"],
    "s": ["Slick", "Sunday", "Slots", "Sweaty", "Showdown"],
    "t": ["Two-Outer", "Tilted", "Tourist", "Tightwad"],
    "u": ["Unlucky", "Undercards"],
    "v": ["Vegas", "Velvet"],
    "w": ["Whiffin'", "Wide-Eyed", "Wagerin'"],
    "x": ["Extra-Loose"],
    "y": ["Yappin'", "Young-Gun"],
    "z": ["Zonked", "Zero-Fold"],
}
_FISH_NAME_FALLBACK = ["Lucky", "Lowball", "Greenhorn", "Sunday"]


def make_fish_name(name: str, rng: Optional[random.Random] = None) -> str:
    """Christen a tourist handle: an alliterative adjective + the player's first name.

    Always keeps the player's own first name and (for A-Z initials) alliterates
    with it. Empty name falls back to a generic handle.
    """
    if rng is None:
        rng = random.Random()
    first = (name or "").strip().split()[0] if (name or "").strip() else "Stranger"
    bank = FISH_NAME_ADJECTIVES.get(first[0].lower(), _FISH_NAME_FALLBACK)
    return f"{rng.choice(bank)} {first}"


# Canned fallback one-liners by vibe, used when the LLM is unavailable (intake
# must never block on the model). Keyed by intensity.
_FALLBACK_BIOS = {
    "spicy": "Talks a big game on the way in — we'll see if the cards agree.",
    "chill": "Seems harmless. Probably just here for the free coffee.",
}


def _fallback_bio(intensity: str) -> str:
    return _FALLBACK_BIOS.get(intensity, _FALLBACK_BIOS["chill"])


def generate_intake_persona(
    name: str,
    *,
    intensity: str = "chill",
    style: str = "",
    owner_id: Optional[str] = None,
    rng: Optional[random.Random] = None,
) -> dict:
    """Build the intake persona: a deterministic alliterative fish-name + a bio.

    The **fish-name is always rule-based** (`make_fish_name`) so it reliably keeps
    the player's own first name and alliterates — the LLM kept dropping the name
    entirely ("Jeff" → "Lost Little Larry"). The LLM is used ONLY for the funny
    one-line bio, with a canned fallback so intake never blocks on the model.
    """
    name = (name or "").strip() or "Stranger"
    fish_name = make_fish_name(name, rng)  # deterministic, always alliterative
    bio = _fallback_bio(intensity)
    try:
        import json as _json

        from core.llm import CallType, LLMClient
        from flask_app import config as flask_config

        client = LLMClient(
            model=flask_config.get_fast_model(), provider=flask_config.get_fast_provider()
        )
        system = (
            "You write a single funny one-liner about a brand-new tourist who just "
            "wandered into an underground poker room and got mistaken for an easy "
            "mark. Warm, playful, PG-13 — never mean. Output strict JSON only."
        )
        user = (
            f"The newcomer is '{fish_name}'. Their table-talk vibe is '{intensity}' "
            f"with a '{style or 'friendly'}' style. Return JSON with one field, "
            f'"bio": ONE funny third-person sentence (max 90 characters) the other '
            f"players can rib them about. Match the '{intensity}' vibe."
        )
        resp = client.complete(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            json_format=True,
            call_type=CallType.CHAT_SUGGESTION,
            owner_id=owner_id,
            prompt_template="career_intake_persona",
        )
        data = _json.loads(resp.content)
        llm_bio = str(data.get("bio") or "").strip()[:140]
        if llm_bio:
            bio = llm_bio
    except Exception:
        logger.warning("[CAREER] intake bio LLM failed; using canned line", exc_info=True)
    return {"fish_name": fish_name, "bio": bio}


def intake_avatar_prompt(fish_name: str, bio: str) -> str:
    """Text prompt for generating the player's avatar from their intake persona.

    The seam lives here; the caller decides *when* to fire image generation
    (it's slow + costly, so intake doesn't block on it) — e.g. a later
    `user_avatar_service.generate_from_prompt(owner_id, prompt)` call.
    """
    return (
        f"A friendly, comedic cartoon portrait of a poker newcomer nicknamed "
        f"'{fish_name}'. {bio} Warm lighting, underground card-room setting, "
        f"character art."
    )


def _table_id_for_stake(stake_label: str, suffix: str = "001") -> str:
    """Stable lobby cardroom id (`cash-table-2-001` style).

    Mirrors `cash_mode.lobby._table_id_for_stake` without importing lobby (which
    would risk an import cycle — lobby pulls in much of the cash stack).
    """
    slug = stake_label[1:] if stake_label.startswith("$") else stake_label
    return f"cash-table-{slug}-{suffix}"


def _ensure_ai_bankroll_row(
    bankroll_repo,
    personality_id: str,
    *,
    sandbox_id: str,
    chip_ledger_repo=None,
    now: datetime,
) -> None:
    """Seed a starting bankroll row for an authored (non-circulating) persona.

    The boot seeder (`ensure_ai_bankrolls_seeded`) only covers circulating
    personas, so Sal and the Scene-0 fish never get a row that way. Without one,
    `debit_bankroll_for_seat` refuses (row-missing → None) and the seat can't be
    funded. This writes the persona's `starting_bankroll` (audited via the
    ledger's first-write `ai_seed`) so the subsequent debit is a clean transfer.
    Idempotent: a live row (last_regen_tick set) is left untouched.
    """
    from cash_mode.bankroll import AIBankrollState

    stored = bankroll_repo.load_ai_bankroll(personality_id, sandbox_id=sandbox_id)
    if stored is not None and stored.last_regen_tick is not None:
        return
    knobs = bankroll_repo.load_personality_knobs(personality_id)
    bankroll_repo.save_ai_bankroll(
        AIBankrollState(
            personality_id=personality_id,
            chips=knobs.starting_bankroll,
            last_regen_tick=now,
        ),
        sandbox_id=sandbox_id,
        chip_ledger_repo=chip_ledger_repo,
    )


def ensure_scene0_seeded(
    *,
    career_progress_repo,
    cash_table_repo,
    bankroll_repo,
    sandbox_id: str,
    owner_id: str,
    chip_ledger_repo=None,
    now: Optional[datetime] = None,
):
    """Idempotently seed + pin the Scene-0 table and switch the keyring on.

    Creates the pinned (`table_type='scripted'`) intimate table — Sal + the fish
    in two seats, the rest open for the player — funding each AI seat by debiting
    that persona's own bankroll (a conservation-safe transfer, the same path the
    lobby seed uses). Flips `career_active=True` and reveals the Scene-0 table on
    the keyring.

    Returns the resulting `CareerProgress`. Idempotent on `scene0_seeded`: a
    second call returns the existing progress unchanged. Best-effort on the seat
    funding — if a debit refuses, that seat is left open rather than minting
    chips (the table still seeds; a missing fish just makes the tutorial duller,
    never breaks conservation).
    """
    if now is None:
        now = datetime.utcnow()

    progress = career_progress_repo.load(sandbox_id, owner_id)
    if progress.scene0_seeded:
        return progress

    # Source-of-truth guard against double-debit: if the Scene-0 table ROW
    # already exists (a prior run debited the seats + saved the table, but the
    # progress save below threw before flipping `scene0_seeded`), do NOT re-seat
    # — re-running `debit_bankroll_for_seat` would double-debit the cast's
    # bankrolls. Reconcile the progress flags from the persisted row and return,
    # mirroring how `ensure_lobby_seeded` treats the cash_tables row as truth.
    existing_table = cash_table_repo.load_table(SCENE0_TABLE_ID, sandbox_id=sandbox_id)
    if existing_table is not None:
        seated_pids = {
            s.get("personality_id") for s in existing_table.seats if s.get("kind") == "ai"
        }
        progress.career_active = True
        progress.scene0_seeded = True
        progress.scene0_table_id = SCENE0_TABLE_ID
        progress.scene0_fish_id = SCENE0_FISH_ID if SCENE0_FISH_ID in seated_pids else None
        if SCENE0_TABLE_ID not in progress.revealed_table_ids:
            progress.revealed_table_ids.append(SCENE0_TABLE_ID)
        career_progress_repo.save(progress, now=now)
        return progress

    from cash_mode.bankroll import debit_bankroll_for_seat
    from cash_mode.stakes_ladder import table_buy_in_window

    _, min_buy_in, max_buy_in = table_buy_in_window(SCENE0_STAKE)

    seats = [open_slot() for _ in range(TABLE_SEAT_COUNT)]

    def _seat(pid: str, seat_index: int, *, is_fish: bool, buy_in_override: int = None) -> bool:
        _ensure_ai_bankroll_row(
            bankroll_repo, pid, sandbox_id=sandbox_id, chip_ledger_repo=chip_ledger_repo, now=now
        )
        knobs = bankroll_repo.load_personality_knobs(pid)
        buy_in = (
            buy_in_override
            if buy_in_override is not None
            else min(round(min_buy_in * knobs.buy_in_multiplier), max_buy_in)
        )
        try:
            debit = debit_bankroll_for_seat(
                bankroll_repo,
                pid,
                buy_in,
                sandbox_id=sandbox_id,
                chip_ledger_repo=chip_ledger_repo,
                now=now,
            )
        except Exception:
            logger.exception("[CAREER] scene0 seed: debit raised for %r — seat left open", pid)
            return False
        if debit is None:
            logger.warning("[CAREER] scene0 seed: debit refused for %r — seat left open", pid)
            return False
        seats[seat_index] = ai_slot_fish(pid, buy_in) if is_fish else ai_slot(pid, buy_in)
        return True

    # Sal sits DEEP — enough to stack Larry in the finale (cover his worst-case
    # late-tutorial stack) so the bust transfers all of Larry's chips to Sal.
    # Drawn from Sal's own (sandbox-scoped) bankroll, so it's a clean transfer.
    fish_knobs = bankroll_repo.load_personality_knobs(SCENE0_FISH_ID)
    fish_buy_in = min(round(min_buy_in * fish_knobs.buy_in_multiplier), max_buy_in)
    _seat(SAL_ID, SCENE0_SAL_SEAT, is_fish=False, buy_in_override=fish_buy_in * 3)
    fish_seated = _seat(SCENE0_FISH_ID, SCENE0_FISH_SEAT, is_fish=True)

    table = CashTableState(
        table_id=SCENE0_TABLE_ID,
        stake_label=SCENE0_STAKE,
        seats=seats,
        created_at=now,
        last_activity_at=now,
        name=SCENE0_TABLE_NAME,
        table_type=SCRIPTED_TABLE_TYPE,
    )
    cash_table_repo.save_table(table, sandbox_id=sandbox_id, now=now)

    progress.career_active = True
    progress.scene0_seeded = True
    progress.scene0_table_id = SCENE0_TABLE_ID
    progress.scene0_fish_id = SCENE0_FISH_ID if fish_seated else None
    if SCENE0_TABLE_ID not in progress.revealed_table_ids:
        progress.revealed_table_ids.append(SCENE0_TABLE_ID)
    career_progress_repo.save(progress, now=now)

    logger.info(
        "[CAREER] scene0 seeded for (sandbox=%s, owner=%s): %s (fish=%s)",
        sandbox_id,
        owner_id,
        SCENE0_TABLE_ID,
        fish_seated,
    )
    return progress


def classify_new_player(progress, existing_tables: List[CashTableState]) -> str:
    """Decide how to treat a sandbox on first sight in the career era.

    Pure classification (the lobby applies the side effects). Returns:
      - ``"noop"`` — the sandbox is already in or past the flow; do nothing.
      - ``"seed"`` — a truly brand-new sandbox (no tables yet) → run Scene 0 and
        switch the keyring on.
      - ``"grandfather"`` — an existing world (tables already seeded) with no
        career row → leave the keyring OFF so the player keeps the full lobby.

    `existing_tables` must be read BEFORE the boot lobby seed creates the
    cardrooms, since "no tables" is the brand-new signal.
    """
    if progress.career_active or progress.scene0_seeded or progress.tutorial_complete:
        return "noop"
    return "seed" if not existing_tables else "grandfather"


def visible_tables(tables: List[CashTableState], progress) -> List[CashTableState]:
    """Filter a table list to what the player may SEE (the keyring).

    When the keyring is off (`career_active` False — legacy/grandfathered
    sandboxes, and the safe default) every table shows: exactly today's
    behavior. When on, only scripted tutorial tables and revealed cardrooms
    show; the rest of the world runs but stays off the player's view.
    """
    if not progress.career_active:
        return tables
    revealed = set(progress.revealed_table_ids)
    return [
        t for t in tables
        if t.table_type == SCRIPTED_TABLE_TYPE or t.table_id in revealed
    ]


def evaluate_first_vouch(progress, *, session_hands: int, fish_pnl: int) -> bool:
    """The scripted Scene-0 graduation gate (M1).

    Fires once when, at the Scene-0 table, the player has played at least
    `MIN_VOUCH_HANDS` this session AND is up at least `FISH_WIN_THRESHOLD` chips
    on the fish — "you waited for the spot, then you took it." Deliberately
    crude: the real relationship-driven model is M2. Returns False if Scene-0 has
    already graduated (`tutorial_complete`) so the vouch fires exactly once.
    """
    if not progress.career_active or progress.tutorial_complete:
        return False
    if session_hands < MIN_VOUCH_HANDS:
        return False
    return fish_pnl >= FISH_WIN_THRESHOLD


def pick_home_court(
    rng: random.Random, *, exclude: Optional[set] = None
) -> Optional[Tuple[str, str]]:
    """Choose the cardroom the first vouch reveals — a random $2 room (lateral).

    Returns `(table_id, display_name)` or None if every home-court candidate is
    already revealed. Reads the lobby config lazily to avoid an import cycle.
    """
    from cash_mode.lobby_config import LOBBY_TABLES

    exclude = exclude or set()
    candidates = [
        (_table_id_for_stake(HOME_COURT_STAKE, entry["id_suffix"]), entry["name"])
        for entry in LOBBY_TABLES.get(HOME_COURT_STAKE, [])
    ]
    candidates = [c for c in candidates if c[0] not in exclude]
    if not candidates:
        return None
    return rng.choice(candidates)


def fire_first_vouch(
    *,
    career_progress_repo,
    sandbox_id: str,
    owner_id: str,
    rng: Optional[random.Random] = None,
    now: Optional[datetime] = None,
):
    """Reveal the home court, mark Scene-0 complete, and return the ticker beat.

    Mutates and persists `CareerProgress` (tutorial_complete, home court on the
    keyring, Sal's one vouch spent) and records an `EVENT_VOUCH` LobbyEvent in
    the in-memory ring so the lobby feed shows it. Returns `(progress, event)`,
    or `(progress, None)` if there was no room left to reveal (caller then emits
    nothing). The caller is responsible for any immediate socket push for
    in-session arrival.
    """
    if now is None:
        now = datetime.utcnow()
    if rng is None:
        rng = random.Random()

    from cash_mode.activity import (
        EVENT_VOUCH,
        LobbyEvent,
        format_vouch_message,
        record_event,
    )

    progress = career_progress_repo.load(sandbox_id, owner_id)
    home = pick_home_court(rng, exclude=set(progress.revealed_table_ids))
    if home is None:
        logger.warning(
            "[CAREER] first vouch: no home-court candidate left for (sandbox=%s, owner=%s)",
            sandbox_id,
            owner_id,
        )
        # Still mark graduated so the gate doesn't re-fire every hand.
        progress.tutorial_complete = True
        if SAL_ID not in progress.vouched_by:
            progress.vouched_by.append(SAL_ID)
        career_progress_repo.save(progress, now=now)
        return progress, None

    home_court_id, home_court_name = home
    progress.tutorial_complete = True
    progress.home_court_table_id = home_court_id
    if home_court_id not in progress.revealed_table_ids:
        progress.revealed_table_ids.append(home_court_id)
    if SAL_ID not in progress.vouched_by:
        progress.vouched_by.append(SAL_ID)
    career_progress_repo.save(progress, now=now)

    event = LobbyEvent(
        type=EVENT_VOUCH,
        table_id=home_court_id,
        stake_label=HOME_COURT_STAKE,
        personality_id=SAL_ID,
        name=SAL_NAME,
        reason="",
        message=format_vouch_message(SAL_NAME, HOME_COURT_STAKE, home_court_name),
        created_at=now.isoformat(),
        sandbox_id=sandbox_id,
    )
    record_event(event)
    logger.info(
        "[CAREER] first vouch fired: %s vouched (sandbox=%s, owner=%s) into %s (%s)",
        SAL_NAME,
        sandbox_id,
        owner_id,
        home_court_id,
        home_court_name,
    )
    return progress, event
