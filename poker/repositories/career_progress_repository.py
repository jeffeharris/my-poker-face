"""Repository for the v124 `career_progress` surface.

Per-(sandbox, owner) narrative state for the Act-1 career-progression spine
(`docs/plans/CASH_MODE_CAREER_PROGRESSION.md`). The lobby is a *keyring*, not a
menu: most cardrooms start invisible and appear only once an AI has vouched the
player into them. This repo holds that keyring plus the Scene-0 tutorial
bookkeeping.

One JSON blob per row so the shape can evolve without a migration per field:

  - `revealed_table_ids` (list[str]) — the keyring. Cardrooms the player may SEE
    in the lobby. New players start empty; each vouch appends one room.
  - `scene0_seeded` (bool) / `scene0_table_id` (str) / `scene0_fish_id` (str) —
    the pinned intimate tutorial table, so seeding is idempotent and the vouch
    trigger knows which fish's PnL to measure.
  - `tutorial_complete` (bool) — Scene-0 graduated (the first vouch fired).
  - `home_court_table_id` (str|None) — the random cardroom the first vouch
    revealed; the room where the player "comes up".
  - `vouched_by` (list[str]) — append-only personality_ids that have already
    spent their one vouch (v1: one vouch per AI).

The world economy runs across ALL tables regardless of this state — the lobby
just filters what it RENDERS — so nothing here gates the sim. Sandbox-keyed so a
fresh save restarts the keyring. Schema is created by
`SchemaManager.ensure_schema()` (v124 migration); this class only touches data.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


@dataclass
class CareerProgress:
    """Decoded `career_progress.progress_json` for one (sandbox, owner).

    A missing row is equivalent to a default-constructed instance: a
    brand-new player with an empty keyring who hasn't been seeded into
    Scene-0 yet. `load` returns this default rather than None so callers
    never special-case "no row".
    """

    sandbox_id: str
    owner_id: str
    # Master switch for the keyring. False (the default, and what a missing or
    # malformed row decodes to) means "legacy / grandfathered" — the lobby
    # filter is BYPASSED and every table shows, i.e. exactly today's behavior.
    # Only a confirmed brand-new sandbox that gets seeded into Scene-0 flips
    # this True. This makes the safe failure mode "show everything" rather than
    # "hide everything", so the keyring can never silently blank an existing
    # playtester's lobby.
    career_active: bool = False
    # The Lucky Stack intake (the cold open): once the player gives a name and a
    # tidbit they're christened a fish-name. `player_name` is what they chose
    # ("Jeff"); `fish_name` is the tourist handle the room assigns ("Juke Joint
    # Jeff") — worn at the Scene-0 table and SHED at the first vouch.
    intake_complete: bool = False
    player_name: Optional[str] = None
    fish_name: Optional[str] = None
    # The table-talk vibe the player picked at intake (introduces quick-chat):
    # `chat_intensity` is 'chill' | 'spicy' (seeds the quick-chat default);
    # `chat_style` is a quick-chat tone (needle/befriend/…). Both feed the LLM
    # that writes the fish-name + bio one-liner.
    chat_intensity: Optional[str] = None
    chat_style: Optional[str] = None
    revealed_table_ids: List[str] = field(default_factory=list)
    scene0_seeded: bool = False
    scene0_table_id: Optional[str] = None
    scene0_fish_id: Optional[str] = None
    tutorial_complete: bool = False
    home_court_table_id: Optional[str] = None
    vouched_by: List[str] = field(default_factory=list)
    # Cold-load durability for scripted table scenes (Scene 0 and any future
    # ones). Keyed by scene_id → {"idx": int, "passed": int, "complete": bool}.
    # The live scene position otherwise lives only in in-memory game_data, so a
    # backend restart / TTL eviction / >2h idle would restart the scene from the
    # top and lose the rigged deck. Persisting it here lets the hand-boundary
    # driver restore where it left off. Generic (not scene0-specific) so the
    # reusable scene system can lean on the same store.
    scene_progress: Dict[str, dict] = field(default_factory=dict)
    # One-shot: the home-court table Sal should ESCORT the player to in the lobby
    # right after graduation (his portrait + a "let me walk ya over" line, pointing
    # at the revealed table). Set by the first vouch, served+cleared by the lobby
    # the first time it renders the handoff. None = no pending handoff.
    mentor_intro_table_id: Optional[str] = None
    # One-shot: the comped Scene-0 seed has been returned to the bank pool on
    # graduation (you were mistaken for a fish; the house comp goes back, so you
    # enter the lobby with nothing and Sal stakes your first real seat). True once
    # the return has fired, so it never double-returns. See the lobby route.
    comp_returned: bool = False

    def has_vouched(self, personality_id: str) -> bool:
        """True if `personality_id` has already spent its one vouch (v1 rule)."""
        return personality_id in self.vouched_by

    def is_revealed(self, table_id: str) -> bool:
        """True if `table_id` is on the keyring (visible in the lobby)."""
        return table_id in self.revealed_table_ids

    def to_json(self) -> str:
        """Serialize just the blob fields (sandbox_id/owner_id live in columns)."""
        return json.dumps(
            {
                "career_active": self.career_active,
                "intake_complete": self.intake_complete,
                "player_name": self.player_name,
                "fish_name": self.fish_name,
                "chat_intensity": self.chat_intensity,
                "chat_style": self.chat_style,
                "revealed_table_ids": self.revealed_table_ids,
                "scene0_seeded": self.scene0_seeded,
                "scene0_table_id": self.scene0_table_id,
                "scene0_fish_id": self.scene0_fish_id,
                "tutorial_complete": self.tutorial_complete,
                "home_court_table_id": self.home_court_table_id,
                "vouched_by": self.vouched_by,
                "scene_progress": self.scene_progress,
                "mentor_intro_table_id": self.mentor_intro_table_id,
                "comp_returned": self.comp_returned,
            }
        )

    @classmethod
    def from_row(cls, sandbox_id: str, owner_id: str, progress_json: str) -> "CareerProgress":
        """Decode a stored row into a `CareerProgress`.

        Tolerant of a malformed/empty blob (degrades to defaults) so a bad
        write can never wedge the lobby — the narrative layer is read-side
        and a corrupt row just reads as "brand-new player".
        """
        try:
            blob = json.loads(progress_json) if progress_json else {}
        except (TypeError, ValueError):
            logger.warning(
                "[CAREER] malformed progress_json for (%s, %s); using defaults",
                sandbox_id,
                owner_id,
            )
            blob = {}
        return cls(
            sandbox_id=sandbox_id,
            owner_id=owner_id,
            career_active=bool(blob.get("career_active", False)),
            intake_complete=bool(blob.get("intake_complete", False)),
            player_name=blob.get("player_name"),
            fish_name=blob.get("fish_name"),
            chat_intensity=blob.get("chat_intensity"),
            chat_style=blob.get("chat_style"),
            revealed_table_ids=list(blob.get("revealed_table_ids") or []),
            scene0_seeded=bool(blob.get("scene0_seeded", False)),
            scene0_table_id=blob.get("scene0_table_id"),
            scene0_fish_id=blob.get("scene0_fish_id"),
            tutorial_complete=bool(blob.get("tutorial_complete", False)),
            home_court_table_id=blob.get("home_court_table_id"),
            vouched_by=list(blob.get("vouched_by") or []),
            scene_progress=dict(blob.get("scene_progress") or {}),
            mentor_intro_table_id=blob.get("mentor_intro_table_id"),
            comp_returned=bool(blob.get("comp_returned", False)),
        )


class CareerProgressRepository(BaseRepository):
    """CRUD for `career_progress` (read-merge-write on a JSON blob)."""

    def load(self, sandbox_id: str, owner_id: str) -> CareerProgress:
        """Return the (sandbox, owner) progress, or a fresh default if no row.

        Never returns None — a missing row is a brand-new player, which the
        default-constructed `CareerProgress` already represents.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT progress_json FROM career_progress
                WHERE sandbox_id = ? AND owner_id = ?
                """,
                (sandbox_id, owner_id),
            ).fetchone()
        if not row:
            return CareerProgress(sandbox_id=sandbox_id, owner_id=owner_id)
        return CareerProgress.from_row(sandbox_id, owner_id, row["progress_json"])

    def save(self, progress: CareerProgress, *, now: Optional[datetime] = None) -> None:
        """Upsert the whole blob for (sandbox, owner).

        Callers mutate a `CareerProgress` loaded via `load` and write it back,
        so this is a full-row replace rather than a field-level merge — the
        in-memory object IS the merge. Single-writer per (sandbox, owner) in
        practice (the lobby/hand-boundary hooks hold the per-sandbox seat lock),
        so no read-modify-write race window.
        """
        if now is None:
            now = datetime.utcnow()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO career_progress (sandbox_id, owner_id, progress_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(sandbox_id, owner_id) DO UPDATE SET
                    progress_json = excluded.progress_json,
                    updated_at = excluded.updated_at
                """,
                (progress.sandbox_id, progress.owner_id, progress.to_json(), now.isoformat()),
            )
