"""Game state repository — game CRUD, messages, AI state, emotional/controller state, opponent models."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from poker.poker_state_machine import PokerPhase, PokerStateMachine
from poker.repositories.base_repository import BaseRepository, retry_on_lock
from poker.repositories.serialization import restore_state_from_dict

logger = logging.getLogger(__name__)


@dataclass
class SavedGame:
    """Represents a saved game with metadata."""

    game_id: str
    created_at: datetime
    updated_at: datetime
    phase: str
    num_players: int
    pot_size: float
    game_state_json: str
    owner_id: Optional[str] = None
    owner_name: Optional[str] = None


class GameRepository(BaseRepository):
    """Repository for game state persistence.

    Handles game CRUD, messages, AI player state, emotional/controller state,
    opponent models, and tournament tracker.
    """

    # --- Game CRUD ---

    def save_coach_mode(self, game_id: str, mode: str) -> None:
        """Persist coach mode preference for a game."""
        with self._get_connection() as conn:
            conn.execute("UPDATE games SET coach_mode = ? WHERE game_id = ?", (mode, game_id))

    def load_coach_mode(self, game_id: str) -> str:
        """Load coach mode preference for a game. Defaults to 'off'."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT coach_mode FROM games WHERE game_id = ?", (game_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] else 'off'

    # --- Game CRUD ---

    @retry_on_lock()
    def save_game(
        self,
        game_id: str,
        state_machine: PokerStateMachine,
        owner_id: Optional[str] = None,
        owner_name: Optional[str] = None,
        llm_configs: Optional[Dict] = None,
    ) -> None:
        """Save a game state to the database.

        Args:
            game_id: The game identifier
            state_machine: The game's state machine
            owner_id: The owner/user ID
            owner_name: The owner's display name
            llm_configs: Dict with 'player_llm_configs' and 'default_llm_config'
        """
        game_state = state_machine.game_state

        state_dict = game_state.to_dict()
        state_dict['current_phase'] = state_machine.current_phase.value
        state_dict['current_hand_seed'] = state_machine.current_hand_seed
        # Persist state-machine fields that aren't on game_state — losing them
        # used to reset hand_count to 0 (re-running blind escalation from
        # scratch) and revert blind_config to its defaults (silently dropping
        # the user's max_blind cap from custom game settings).
        state_dict['stats_hand_count'] = state_machine._state.stats.hand_count
        bc = state_machine._state.blind_config
        state_dict['blind_config'] = {
            'growth': bc.growth,
            'hands_per_level': bc.hands_per_level,
            'max_blind': bc.max_blind,
        }

        game_json = json.dumps(state_dict)
        llm_configs_json = json.dumps(llm_configs) if llm_configs else None

        with self._get_connection() as conn:
            # Use ON CONFLICT DO UPDATE to preserve columns not being updated
            # (like debug_capture_enabled) instead of INSERT OR REPLACE which
            # deletes and re-inserts, resetting unspecified columns to defaults
            conn.execute(
                """
                INSERT INTO games
                (game_id, updated_at, phase, num_players, pot_size, game_state_json, owner_id, owner_name, llm_configs_json)
                VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id) DO UPDATE SET
                    updated_at = CURRENT_TIMESTAMP,
                    phase = excluded.phase,
                    num_players = excluded.num_players,
                    pot_size = excluded.pot_size,
                    game_state_json = excluded.game_state_json,
                    owner_id = excluded.owner_id,
                    owner_name = excluded.owner_name,
                    llm_configs_json = COALESCE(excluded.llm_configs_json, games.llm_configs_json)
            """,
                (
                    game_id,
                    state_machine.current_phase.value,
                    len(game_state.players),
                    game_state.pot['total'],
                    game_json,
                    owner_id,
                    owner_name,
                    llm_configs_json,
                ),
            )

    def load_game(self, game_id: str) -> Optional[PokerStateMachine]:
        """Load a game state from the database."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM games WHERE game_id = ?", (game_id,))
            row = cursor.fetchone()

            if not row:
                return None

            state_dict = json.loads(row['game_state_json'])
            game_state = restore_state_from_dict(state_dict)

            # Restore the phase - handle both int and string values
            try:
                phase_value = state_dict.get('current_phase', 0)
                if isinstance(phase_value, str):
                    phase_value = int(phase_value)
                phase = PokerPhase(phase_value)
            except (ValueError, KeyError):
                logger.warning(
                    f"[RESTORE] Could not restore phase {state_dict.get('current_phase')}, using INITIALIZING_HAND"
                )
                phase = PokerPhase.INITIALIZING_HAND

            # Create state machine with the loaded state and phase
            sm = PokerStateMachine.from_saved_state(
                game_state,
                phase,
                blind_config=state_dict.get('blind_config'),
                hand_count=state_dict.get('stats_hand_count', 0),
            )

            # Restore deck seed so the in-progress hand can be recorded with
            # its seed. Mark provided=False so the seed is treated as already
            # consumed — without this, the next hand_over_transition would
            # see hand_seed_provided=True and reuse this seed for a fresh
            # deal, producing back-to-back hands with the same shuffle but a
            # rotated dealer (visible as "same hand, shifted hole cards").
            saved_seed = state_dict.get('current_hand_seed')
            if saved_seed is not None:
                sm._state = sm._state.with_hand_seed(saved_seed, provided=False)

            return sm

    def load_llm_configs(self, game_id: str) -> Optional[Dict]:
        """Load LLM configs for a game.

        Args:
            game_id: The game identifier

        Returns:
            Dict with 'player_llm_configs' and 'default_llm_config', or None if not found
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT llm_configs_json FROM games WHERE game_id = ?", (game_id,)
            )
            row = cursor.fetchone()

            if not row or not row['llm_configs_json']:
                return None

            return json.loads(row['llm_configs_json'])

    def get_game_owner_info(self, game_id: str) -> Optional[Dict[str, Optional[str]]]:
        """Get owner metadata for a game.

        Args:
            game_id: The game identifier

        Returns:
            Dict with owner_id and owner_name, or None if game does not exist
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT owner_id, owner_name FROM games WHERE game_id = ?",
                (game_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            return {
                'owner_id': row['owner_id'],
                'owner_name': row['owner_name'],
            }

    @retry_on_lock()
    def save_tournament_tracker(self, game_id: str, tracker) -> None:
        """Save tournament tracker state to the database.

        Args:
            game_id: The game identifier
            tracker: TournamentTracker instance or dict from to_dict()
        """
        if hasattr(tracker, 'to_dict'):
            tracker_dict = tracker.to_dict()
        else:
            tracker_dict = tracker

        tracker_json = json.dumps(tracker_dict)

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tournament_tracker (game_id, tracker_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(game_id) DO UPDATE SET
                    tracker_json = excluded.tracker_json,
                    updated_at = CURRENT_TIMESTAMP
            """,
                (game_id, tracker_json),
            )

    def load_tournament_tracker(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Load tournament tracker state from the database.

        Args:
            game_id: The game identifier

        Returns:
            Dict that can be passed to TournamentTracker.from_dict(), or None if not found
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT tracker_json FROM tournament_tracker WHERE game_id = ?", (game_id,)
            )
            row = cursor.fetchone()

            if not row:
                return None

            return json.loads(row[0])

    def list_games(
        self, owner_id: Optional[str] = None, limit: int = 20, offset: int = 0
    ) -> List[SavedGame]:
        """List saved games, most recently updated first. Filter by owner_id if provided."""
        with self._get_connection() as conn:
            if owner_id:
                cursor = conn.execute(
                    """
                    SELECT * FROM games
                    WHERE owner_id = ?
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?
                """,
                    (owner_id, limit, offset),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM games
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?
                """,
                    (limit, offset),
                )

            games = []
            for row in cursor:
                games.append(
                    SavedGame(
                        game_id=row['game_id'],
                        created_at=datetime.fromisoformat(row['created_at']),
                        updated_at=datetime.fromisoformat(row['updated_at']),
                        phase=row['phase'],
                        num_players=row['num_players'],
                        pot_size=row['pot_size'],
                        game_state_json=row['game_state_json'],
                        owner_id=row['owner_id'],
                        owner_name=row['owner_name'],
                    )
                )

            return games

    def delete_game(self, game_id: str) -> None:
        """Delete a game's active state (save data, snapshots, AI state, messages).

        Historical data (hand_history, tournament_results, pressure_events, etc.)
        is intentionally preserved so post-session analytics survive cash leave
        / cleanup. PRAGMA foreign_keys is not set on these connections, so each
        per-game table is cleared explicitly; the FK declarations on preserved
        history tables become harmless once the games row is gone.
        """
        with self._get_connection() as conn:
            conn.execute("DELETE FROM personality_snapshots WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM ai_player_state WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM game_messages WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM tournament_tracker WHERE game_id = ?", (game_id,))
            # pressure_events intentionally preserved — they record per-hand
            # drama events that the admin dashboard and player_career_stats
            # read back for post-session analytics. Cash leave used to wipe
            # them along with the live game row (full 35-hand sessions
            # ending with zero rows), which contradicted the docstring's
            # "historical data preserved" promise.
            conn.execute("DELETE FROM controller_state WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM games WHERE game_id = ?", (game_id,))

    # --- Messages ---

    def save_message(self, game_id: str, message_type: str, message_text: str) -> None:
        """Save a game message/event."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO game_messages (game_id, message_type, message_text)
                VALUES (?, ?, ?)
            """,
                (game_id, message_type, message_text),
            )

    def load_messages(self, game_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Load recent messages for a game."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM game_messages
                WHERE game_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (game_id, limit),
            )

            messages = []
            for row in cursor:
                # Parse "sender: content" format back into separate fields
                text = row['message_text']
                if ': ' in text:
                    sender, content = text.split(': ', 1)
                else:
                    sender = 'System'
                    content = text
                messages.append(
                    {
                        'id': str(row['id']),
                        'timestamp': row['timestamp'],
                        'type': row['message_type'],
                        'sender': sender,
                        'content': content,
                    }
                )

            return list(reversed(messages))  # Return in chronological order

    # --- AI Player State ---

    @retry_on_lock()
    def save_ai_player_state(
        self,
        game_id: str,
        player_name: str,
        messages: List[Dict[str, str]],
        personality_state: Dict[str, Any],
    ) -> None:
        """Save AI player conversation history and personality state."""
        with self._get_connection() as conn:
            conversation_history = json.dumps(messages)
            personality_json = json.dumps(personality_state)

            conn.execute(
                """
                INSERT OR REPLACE INTO ai_player_state
                (game_id, player_name, conversation_history, personality_state)
                VALUES (?, ?, ?, ?)
            """,
                (game_id, player_name, conversation_history, personality_json),
            )

    def load_ai_player_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all AI player states for a game."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT player_name, conversation_history, personality_state
                FROM ai_player_state
                WHERE game_id = ?
            """,
                (game_id,),
            )

            ai_states = {}
            for row in cursor.fetchall():
                ai_states[row['player_name']] = {
                    'messages': json.loads(row['conversation_history']),
                    'personality_state': json.loads(row['personality_state']),
                }

            return ai_states

    @retry_on_lock()
    def save_personality_snapshot(
        self,
        game_id: str,
        player_name: str,
        hand_number: int,
        traits: Dict[str, Any],
        pressure_levels: Optional[Dict[str, float]] = None,
    ) -> None:
        """Save a snapshot of personality state for elasticity tracking.

        Uses INSERT OR IGNORE so a retry after a successful-but-uncommitted
        write doesn't insert a duplicate snapshot row (the table has no
        UNIQUE constraint on (game_id, player_name, hand_number) and an
        autoincrement PK).
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO personality_snapshots
                (player_name, game_id, hand_number, personality_traits, pressure_levels)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    player_name,
                    game_id,
                    hand_number,
                    json.dumps(traits),
                    json.dumps(pressure_levels or {}),
                ),
            )

    # --- Emotional State ---

    @retry_on_lock()
    def delete_controller_state_for_game(self, game_id: str) -> None:
        """Delete all controller states for a game."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM controller_state WHERE game_id = ?", (game_id,))

    # --- Controller State ---

    @retry_on_lock()
    def save_controller_state(
        self,
        game_id: str,
        player_name: str,
        psychology: Dict[str, Any],
        prompt_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Save unified psychology state and prompt config for a player.

        Args:
            game_id: The game identifier
            player_name: The player's name
            psychology: Dict from PlayerPsychology.to_dict()
            prompt_config: Dict from PromptConfig.to_dict() (optional)
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO controller_state
                (game_id, player_name, psychology_json, prompt_config_json, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
                (
                    game_id,
                    player_name,
                    json.dumps(psychology) if psychology else None,
                    json.dumps(prompt_config) if prompt_config else None,
                ),
            )

    @staticmethod
    def _build_controller_state_dict(row, player_name: str = '') -> Dict[str, Any]:
        """Build a controller state dict from a database row.

        Pre-v83 rows lack `psychology_json` (NULL) and may have populated
        `tilt_state_json` / `elastic_personality_json`. The legacy fields
        are exposed unchanged so any downstream caller that still consumes
        them can keep working; new code should read `psychology`.
        """
        psychology = None
        try:
            if row['psychology_json']:
                psychology = json.loads(row['psychology_json'])
        except (KeyError, IndexError):
            if player_name:
                logger.debug(
                    f"psychology_json column not found for {player_name}; " "fresh-init fallback"
                )

        prompt_config = None
        try:
            if row['prompt_config_json']:
                prompt_config = json.loads(row['prompt_config_json'])
        except (KeyError, IndexError):
            if player_name:
                logger.warning(
                    f"prompt_config_json column not found for {player_name}, using defaults"
                )

        return {
            'psychology': psychology,
            'tilt_state': json.loads(row['tilt_state_json']) if row['tilt_state_json'] else None,
            'elastic_personality': json.loads(row['elastic_personality_json'])
            if row['elastic_personality_json']
            else None,
            'prompt_config': prompt_config,
        }

    def load_controller_state(self, game_id: str, player_name: str) -> Optional[Dict[str, Any]]:
        """Load controller state for a player.

        Returns:
            Dict with 'psychology' (v2.1 unified state), legacy
            'tilt_state' / 'elastic_personality' (NULL on new writes),
            and 'prompt_config' keys, or None if not found.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT tilt_state_json, elastic_personality_json, prompt_config_json, psychology_json
                FROM controller_state
                WHERE game_id = ? AND player_name = ?
            """,
                (game_id, player_name),
            )

            row = cursor.fetchone()
            if not row:
                return None

            return self._build_controller_state_dict(row, player_name)

    def load_all_controller_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all controller states for a game.

        Returns:
            Dict mapping player_name -> controller state dict
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT player_name, tilt_state_json, elastic_personality_json, prompt_config_json, psychology_json
                FROM controller_state
                WHERE game_id = ?
            """,
                (game_id,),
            )

            # Per-row guard: one corrupt psychology_json/tilt_state_json must
            # not wipe every player's restored psychology. Log+skip the bad
            # row, keep the good ones. (Without this a single bad row threw and
            # the caller's wholesale except left ALL AIs at default tilt.)
            states: Dict[str, Dict[str, Any]] = {}
            for row in cursor.fetchall():
                player_name = row['player_name']
                try:
                    states[player_name] = self._build_controller_state_dict(row, player_name)
                except Exception as e:
                    logger.error(
                        "Skipping corrupt controller_state row for %r in game %r: %s",
                        player_name,
                        game_id,
                        e,
                        exc_info=True,
                    )
            return states

    # --- Opponent Models ---

    @retry_on_lock()
    def save_opponent_models(self, game_id: str, opponent_model_manager) -> None:
        """Save opponent models for a game.

        Args:
            game_id: The game identifier
            opponent_model_manager: OpponentModelManager instance or dict from to_dict()
        """
        if hasattr(opponent_model_manager, 'to_dict'):
            models_dict = opponent_model_manager.to_dict()
        else:
            models_dict = opponent_model_manager

        if not models_dict:
            return

        # The manager's to_dict() injects a __name_to_id__ sidecar key
        # at the top level for round-trip preservation. Extract it
        # before iterating observer entries so we have a name→id map
        # to fall back on for rows where the model row itself doesn't
        # carry an id (legacy snapshots written before commit 5e74854b).
        name_to_id = (
            models_dict.pop('__name_to_id__', None) if isinstance(models_dict, dict) else None
        )

        def _resolve_id(model_data, name):
            # Prefer per-row id (set when register_player_id ran) and
            # fall back to the manager-level registry when present.
            row_id = None
            if isinstance(model_data, dict):
                # For opponent_id: model_data['opponent_id'] is the row's opp id
                # We use this helper for both observer + opponent slots, so
                # the caller passes the explicit per-row field.
                row_id = model_data
            if row_id:
                return row_id
            if name_to_id:
                return name_to_id.get(name)
            return None

        with self._get_connection() as conn:
            # Detect whether the v86 id columns are present so this save
            # path stays compatible with pre-v86 schemas during a rolling
            # migration window.
            opp_cols = {row[1] for row in conn.execute("PRAGMA table_info(opponent_models)")}
            has_id_cols = 'observer_id' in opp_cols and 'opponent_id' in opp_cols
            has_applied_col = 'lifetime_applied_json' in opp_cols

            # Preserve the lifetime-fold high-water mark across the
            # delete+reinsert below. INSERT OR REPLACE drops any column not
            # listed (lifetime_applied_json isn't), which would reset the
            # mark to NULL and make the post-save fold re-add the full count
            # every save (double-counting). Snapshot it here, restore it
            # after the reinserts. Keyed by (observer_name, opponent_name) —
            # the stable identity within a game's models.
            applied_marks = {}
            if has_applied_col:
                applied_marks = {
                    (r['observer_name'], r['opponent_name']): r['lifetime_applied_json']
                    for r in conn.execute(
                        "SELECT observer_name, opponent_name, lifetime_applied_json "
                        "FROM opponent_models WHERE game_id = ? "
                        "AND lifetime_applied_json IS NOT NULL",
                        (game_id,),
                    )
                }

            # Clear existing models for this game
            conn.execute("DELETE FROM opponent_models WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM memorable_hands WHERE game_id = ?", (game_id,))

            # Save each observer -> opponent -> model
            for observer_name, opponents in models_dict.items():
                for opponent_name, model_data in opponents.items():
                    tendencies = model_data.get('tendencies', {})

                    # OpponentModel.to_dict() uses 'narrative_observations' key
                    narrative_obs = model_data.get('narrative_observations', [])
                    notes = json.dumps(narrative_obs) if narrative_obs else None

                    # Resolve ids: prefer values written on the model dict
                    # (set when OpponentModel was created with personality
                    # ids known), fall back to the manager-level registry.
                    observer_id = model_data.get('observer_id')
                    opponent_id = model_data.get('opponent_id')
                    if observer_id is None and name_to_id:
                        observer_id = name_to_id.get(observer_name)
                    if opponent_id is None and name_to_id:
                        opponent_id = name_to_id.get(opponent_name)

                    if has_id_cols:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO opponent_models
                            (game_id, observer_name, opponent_name,
                             observer_id, opponent_id,
                             hands_observed,
                             vpip, pfr, aggression_factor, fold_to_cbet,
                             bluff_frequency, showdown_win_rate, recent_trend, notes,
                             tendencies_json, last_updated)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        """,
                            (
                                game_id,
                                observer_name,
                                opponent_name,
                                observer_id,
                                opponent_id,
                                tendencies.get('hands_observed', 0),
                                tendencies.get('vpip', 0.5),
                                tendencies.get('pfr', 0.5),
                                tendencies.get('aggression_factor', 1.0),
                                tendencies.get('fold_to_cbet', 0.5),
                                tendencies.get('bluff_frequency', 0.3),
                                tendencies.get('showdown_win_rate', 0.5),
                                tendencies.get('recent_trend', 'stable'),
                                notes,
                                json.dumps(tendencies),
                            ),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO opponent_models
                            (game_id, observer_name, opponent_name, hands_observed,
                             vpip, pfr, aggression_factor, fold_to_cbet,
                             bluff_frequency, showdown_win_rate, recent_trend, notes,
                             tendencies_json, last_updated)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        """,
                            (
                                game_id,
                                observer_name,
                                opponent_name,
                                tendencies.get('hands_observed', 0),
                                tendencies.get('vpip', 0.5),
                                tendencies.get('pfr', 0.5),
                                tendencies.get('aggression_factor', 1.0),
                                tendencies.get('fold_to_cbet', 0.5),
                                tendencies.get('bluff_frequency', 0.3),
                                tendencies.get('showdown_win_rate', 0.5),
                                tendencies.get('recent_trend', 'stable'),
                                notes,
                                json.dumps(tendencies),
                            ),
                        )

                    # Save memorable hands
                    memorable_hands = model_data.get('memorable_hands', [])
                    for hand in memorable_hands:
                        conn.execute(
                            """
                            INSERT INTO memorable_hands
                            (game_id, observer_name, opponent_name, hand_id,
                             memory_type, impact_score, narrative, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                            (
                                game_id,
                                observer_name,
                                opponent_name,
                                hand.get('hand_id', 0),
                                hand.get('memory_type', ''),
                                hand.get('impact_score', 0.0),
                                hand.get('narrative', ''),
                                hand.get('timestamp', datetime.now().isoformat()),
                            ),
                        )

            # Restore the preserved lifetime-fold high-water marks onto the
            # freshly-reinserted rows so the post-save fold computes a correct
            # delta (current − already-applied) instead of re-adding the full
            # count. Rows new this save have no mark and stay NULL (fold then
            # treats the whole count as the first delta — correct).
            for (obs_name, opp_name), applied_json in applied_marks.items():
                conn.execute(
                    "UPDATE opponent_models SET lifetime_applied_json = ? "
                    "WHERE game_id = ? AND observer_name = ? AND opponent_name = ?",
                    (applied_json, game_id, obs_name, opp_name),
                )

            logger.debug(f"Saved opponent models for game {game_id}")

    # Maps the count keys serialized in opponent_models.tendencies_json
    # (OpponentTendencies.to_dict) → the cumulative columns on
    # opponent_observation_lifetime. Counts only — rates derive on read.
    # Integer accumulators: lifetime += delta each fold.
    _LIFETIME_COUNT_FIELDS = {
        'hands_dealt': 'hands_dealt',
        'hands_observed': 'hands_observed',
        '_vpip_count': 'vpip_count',
        '_pfr_count': 'pfr_count',
        '_bet_raise_count': 'bet_raise_count',
        '_call_count': 'call_count',
        '_showdowns': 'showdowns_seen',
        '_showdowns_won': 'showdowns_won',
        # v125 deep postflop counts — numerator/denominator pairs for the
        # Tier-2 reads (rates derive on read via OpponentTendencies).
        '_all_in_count': 'all_in_count',
        '_fold_to_cbet_count': 'fold_to_cbet_count',
        '_cbet_faced_count': 'cbet_faced_count',
        '_cbet_attempt_count': 'cbet_attempt_count',
        '_postflop_seen_as_pfr_count': 'postflop_seen_as_pfr_count',
        '_barrel_count': 'barrel_count',
        '_barrel_opportunity_count': 'barrel_opportunity_count',
        '_third_barrel_count': 'third_barrel_count',
        '_third_barrel_opportunity_count': 'third_barrel_opportunity_count',
        '_postflop_bet_raise_count': 'postflop_bet_raise_count',
        '_postflop_call_count': 'postflop_call_count',
        '_equity_betting_count': 'equity_betting_count',
        '_equity_raising_count': 'equity_raising_count',
        '_equity_calling_count': 'equity_calling_count',
        # v126 preflop opportunity counts — denominators for the player-count-
        # stable vpip_per_voluntary_opportunity / pfr_per_open_opportunity the
        # station/nit exploitation detectors gate on (dossier "the read").
        '_preflop_voluntary_action_count': 'preflop_voluntary_action_count',
        '_preflop_voluntary_opportunities': 'preflop_voluntary_opportunities',
        '_preflop_open_raise_count': 'preflop_open_raise_count',
        '_preflop_open_opportunities': 'preflop_open_opportunities',
    }

    # Float accumulators (v125): the equity-at-action sums. Same delta-fold as
    # the integer counts, but coerced as floats; the polarization means derive
    # on read as sum / count.
    _LIFETIME_SUM_FIELDS = {
        '_equity_betting_sum': 'equity_betting_sum',
        '_equity_raising_sum': 'equity_raising_sum',
        '_equity_calling_sum': 'equity_calling_sum',
    }

    def fold_observations_into_lifetime(self, game_id: str, sandbox_id: Optional[str]) -> int:
        """Fold this game's per-opponent observation counts into the durable
        per-sandbox `opponent_observation_lifetime` rows (Phase 1).

        The Circuit's scouting memory. Called right after
        `save_opponent_models` at each hand-boundary save, but ONLY for
        sandbox-bound games (Circuit cash + Circuit tournaments) — a falsy
        `sandbox_id` makes this a no-op, so other modes never contribute.

        Continuous **delta-fold**: for each (observer_id, opponent_id) pair
        with both ids present, `delta = current_counts − applied`, the
        lifetime row is incremented by `delta`, and the per-game high-water
        mark `opponent_models.lifetime_applied_json` is set to the current
        counts. This is resume-safe (cold-load reuses game_id) and never
        double-counts: re-folding an unchanged game writes nothing.

        Reads what `save_opponent_models` just persisted (counts live in
        `tendencies_json`); kept as a separate method + transaction so the
        hot save path stays untouched. Returns the number of pairs folded.
        """
        if not sandbox_id:
            return 0

        folded = 0
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT observer_id, opponent_id, tendencies_json,
                       lifetime_applied_json
                FROM opponent_models
                WHERE game_id = ?
                """,
                (game_id,),
            ).fetchall()

            for row in rows:
                observer_id = row['observer_id']
                opponent_id = row['opponent_id']
                # Only id'd pairs accumulate lifetime intel; human/ad-hoc
                # rows without stable ids are skipped (nothing reads them).
                # Self-pairs (observer == opponent) are noise — you don't
                # scout yourself — so they're skipped too.
                if not observer_id or not opponent_id or observer_id == opponent_id:
                    continue

                try:
                    current_raw = json.loads(row['tendencies_json'] or '{}')
                except (TypeError, ValueError):
                    continue
                try:
                    applied = json.loads(row['lifetime_applied_json'] or '{}')
                except (TypeError, ValueError):
                    applied = {}

                # Current counts (int) + sums (float), keyed by tendencies key.
                current = {
                    src: int(current_raw.get(src, 0) or 0) for src in self._LIFETIME_COUNT_FIELDS
                }
                current.update(
                    {
                        src: float(current_raw.get(src, 0.0) or 0.0)
                        for src in self._LIFETIME_SUM_FIELDS
                    }
                )
                # Per-column deltas vs the high-water mark. Column order is
                # taken from the field maps so the INSERT below stays in sync
                # automatically as new fields are added.
                int_cols = list(self._LIFETIME_COUNT_FIELDS.values())
                sum_cols = list(self._LIFETIME_SUM_FIELDS.values())
                all_cols = int_cols + sum_cols
                deltas = {
                    col: current[src] - int(applied.get(src, 0) or 0)
                    for src, col in self._LIFETIME_COUNT_FIELDS.items()
                }
                deltas.update(
                    {
                        col: current[src] - float(applied.get(src, 0.0) or 0.0)
                        for src, col in self._LIFETIME_SUM_FIELDS.items()
                    }
                )
                if not any(deltas.values()):
                    continue  # nothing new since last fold

                col_list = ", ".join(all_cols)
                placeholders = ", ".join("?" for _ in all_cols)
                update_set = ",\n                        ".join(
                    f"{col} = {col} + excluded.{col}" for col in all_cols
                )
                conn.execute(
                    f"""
                    INSERT INTO opponent_observation_lifetime
                        (sandbox_id, observer_id, opponent_id,
                         {col_list}, first_seen, last_updated)
                    VALUES (?, ?, ?, {placeholders},
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(sandbox_id, observer_id, opponent_id) DO UPDATE SET
                        {update_set},
                        last_updated    = CURRENT_TIMESTAMP
                    """,
                    (
                        sandbox_id,
                        observer_id,
                        opponent_id,
                        *(deltas[col] for col in all_cols),
                    ),
                )
                conn.execute(
                    """
                    UPDATE opponent_models SET lifetime_applied_json = ?
                    WHERE game_id = ? AND observer_id = ? AND opponent_id = ?
                    """,
                    (json.dumps(current), game_id, observer_id, opponent_id),
                )
                folded += 1

        if folded:
            logger.debug(
                "Folded %d opponent observation(s) into lifetime for game %s " "(sandbox %s)",
                folded,
                game_id,
                sandbox_id,
            )
        return folded

    def load_observation_lifetime(
        self, sandbox_id: str, observer_id: str, opponent_id: str
    ) -> Optional[Dict[str, Any]]:
        """Load the durable lifetime observation COUNTS for a pair in one
        sandbox, or None when no lifetime row exists yet.

        Returns raw cumulative counts only — rates (VPIP/PFR/AF/showdown) are
        derived by the caller through the canonical `OpponentTendencies`
        formula so this repository stays free of strategy-config coupling and
        the rate definitions never drift from the live path.
        """
        # Count/sum columns are sourced from the fold field maps so the read
        # stays in sync with what the fold writes (v125 deep reads included).
        count_cols = list(self._LIFETIME_COUNT_FIELDS.values())
        sum_cols = list(self._LIFETIME_SUM_FIELDS.values())
        with self._get_connection() as conn:
            row = conn.execute(
                f"""
                SELECT {", ".join(count_cols + sum_cols)}, first_seen, last_updated
                FROM opponent_observation_lifetime
                WHERE sandbox_id = ? AND observer_id = ? AND opponent_id = ?
                """,
                (sandbox_id, observer_id, opponent_id),
            ).fetchone()

        if row is None:
            return None
        result: Dict[str, Any] = {col: row[col] or 0 for col in count_cols}
        result.update({col: row[col] or 0.0 for col in sum_cols})
        result['first_seen'] = row['first_seen']
        result['last_updated'] = row['last_updated']
        return result

    # Opportunity denominators the dossier scouting gate's Tier-2 tiers read
    # (kept in sync with `dossier_scouting.SCOUTING_SCHEDULE` sample_fields).
    # Exposed on the roster so the file cabinet's unlock % matches the dossier
    # (hand count alone can't decide a sample-gated tier). Named here rather
    # than imported because this repo (poker/) must not depend on flask_app/.
    _ROSTER_SAMPLE_COLUMNS = (
        'cbet_faced_count',
        'postflop_seen_as_pfr_count',
        'postflop_bet_raise_count',
        'postflop_call_count',
        'barrel_opportunity_count',
        'equity_betting_count',
        'equity_raising_count',
        'equity_calling_count',
    )

    def list_observation_lifetime_for_observer(
        self, sandbox_id: str, observer_id: str
    ) -> List[Dict[str, Any]]:
        """Every opponent this observer has a lifetime observation row for, in
        this sandbox — the roster spine for the file cabinet. Returns
        opponent_id + hands_observed + hands_dealt + last_updated plus the
        Tier-2 opportunity counts (`_ROSTER_SAMPLE_COLUMNS`) so the file
        cabinet can compute the same sample-gated unlock state the dossier
        does. PnL / relationship / names are joined separately. Ordered
        most-observed first.
        """
        sample_cols = ", ".join(self._ROSTER_SAMPLE_COLUMNS)
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT opponent_id, hands_observed, hands_dealt, {sample_cols},
                       first_seen, last_updated
                FROM opponent_observation_lifetime
                WHERE sandbox_id = ? AND observer_id = ?
                  AND opponent_id != observer_id
                ORDER BY hands_observed DESC
                """,
                (sandbox_id, observer_id),
            ).fetchall()
        result = []
        for r in rows:
            entry = {
                'opponent_id': r['opponent_id'],
                'hands_observed': r['hands_observed'] or 0,
                'hands_dealt': r['hands_dealt'] or 0,
                'first_seen': r['first_seen'],
                'last_updated': r['last_updated'],
            }
            entry.update({col: r[col] or 0 for col in self._ROSTER_SAMPLE_COLUMNS})
            result.append(entry)
        return result

    def load_lifetime_memorable_hands(
        self, owner_id: str, opponent_name: str, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Top memorable hands the human (game owner) has logged against
        `opponent_name` across ALL of their games — the durable, cross-game
        view for the dossier (the live builder only sees the active game).

        Scoped by game owner (≈ sandbox under v1's 1:1 ownership). Filters to
        the human-as-observer by matching observer_name to the game's
        owner_name, so AI-observer memories don't leak in. `hand_summary` is
        not persisted (game_repository known gap), so it comes back empty —
        the narrative + impact carry the entry.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT m.hand_id, m.memory_type, m.impact_score, m.narrative,
                       m.created_at
                FROM memorable_hands m
                JOIN games g ON m.game_id = g.game_id
                WHERE g.owner_id = ?
                  AND m.opponent_name = ?
                  AND m.observer_name = g.owner_name
                ORDER BY m.impact_score DESC
                LIMIT ?
                """,
                (owner_id, opponent_name, limit),
            ).fetchall()
        return [
            {
                'hand_id': r['hand_id'],
                'event': r['memory_type'],
                'impact_score': r['impact_score'] or 0.0,
                'narrative': r['narrative'] or '',
                'hand_summary': '',  # not persisted (known gap)
                'timestamp': r['created_at'],
            }
            for r in rows
        ]

    def load_relationship_history(
        self, owner_id: str, opponent_name: str, clash_types=()
    ) -> Dict[str, Any]:
        """Aggregate the human's logged relationship events vs `opponent_name`
        across all their games — the rivalry view for the dossier.

        Owner-scoped, human-as-observer (same scoping as
        `load_lifetime_memorable_hands`). Returns:
          - `counts`: {memory_type: n} over every logged event (clash + chat).
          - `defining`: the single highest-impact "clash" hand (a
            rivalry-defining moment) as {event, impact_score, narrative,
            timestamp}, or None. `clash_types` names which memory_types count
            as clashes (the taxonomy lives in the service layer).
        """
        with self._get_connection() as conn:
            count_rows = conn.execute(
                """
                SELECT m.memory_type AS memory_type, COUNT(*) AS n
                FROM memorable_hands m
                JOIN games g ON m.game_id = g.game_id
                WHERE g.owner_id = ?
                  AND m.opponent_name = ?
                  AND m.observer_name = g.owner_name
                GROUP BY m.memory_type
                """,
                (owner_id, opponent_name),
            ).fetchall()
            counts = {r['memory_type']: r['n'] for r in count_rows}

            defining = None
            clash_types = tuple(clash_types)
            if clash_types:
                placeholders = ",".join("?" for _ in clash_types)
                row = conn.execute(
                    f"""
                    SELECT m.memory_type, m.impact_score, m.narrative,
                           m.created_at
                    FROM memorable_hands m
                    JOIN games g ON m.game_id = g.game_id
                    WHERE g.owner_id = ?
                      AND m.opponent_name = ?
                      AND m.observer_name = g.owner_name
                      AND m.memory_type IN ({placeholders})
                    ORDER BY m.impact_score DESC
                    LIMIT 1
                    """,
                    (owner_id, opponent_name, *clash_types),
                ).fetchone()
                if row is not None:
                    defining = {
                        'event': row['memory_type'],
                        'impact_score': row['impact_score'] or 0.0,
                        'narrative': row['narrative'] or '',
                        'timestamp': row['created_at'],
                    }

        return {'counts': counts, 'defining': defining}

    def load_informant_unlocks(self, sandbox_id: str, observer_id: str, opponent_id: str) -> set:
        """Return the set of dossier section_ids the observer has bought from
        the informant for this opponent in this sandbox (Phase 3)."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT section_id FROM dossier_informant_unlocks
                WHERE sandbox_id = ? AND observer_id = ? AND opponent_id = ?
                """,
                (sandbox_id, observer_id, opponent_id),
            ).fetchall()
        return {row['section_id'] for row in rows}

    def load_all_informant_unlocks_for_observer(
        self, sandbox_id: str, observer_id: str
    ) -> Dict[str, set]:
        """All informant section purchases for this observer in this sandbox,
        keyed opponent_id → set(section_ids). One query for the file cabinet's
        per-opponent unlock status (vs. N calls to load_informant_unlocks)."""
        out: Dict[str, set] = {}
        with self._get_connection() as conn:
            for r in conn.execute(
                """
                SELECT opponent_id, section_id FROM dossier_informant_unlocks
                WHERE sandbox_id = ? AND observer_id = ?
                """,
                (sandbox_id, observer_id),
            ):
                out.setdefault(r['opponent_id'], set()).add(r['section_id'])
        return out

    def record_informant_unlock(
        self,
        sandbox_id: str,
        observer_id: str,
        opponent_id: str,
        section_id: str,
        price_paid: int,
    ) -> bool:
        """Persist an informant section purchase. Idempotent: a section
        already owned is left as-is (INSERT OR IGNORE) and returns False so
        the caller can avoid charging twice; a new row returns True."""
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO dossier_informant_unlocks
                    (sandbox_id, observer_id, opponent_id, section_id,
                     price_paid, purchased_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (sandbox_id, observer_id, opponent_id, section_id, int(price_paid)),
            )
            return cur.rowcount > 0

    def load_opponent_models(self, game_id: str) -> Dict[str, Any]:
        """Load opponent models for a game.

        Returns:
            Dict suitable for OpponentModelManager.from_dict(), or empty dict if not found
        """
        models_dict = {}

        with self._get_connection() as conn:
            # Load all opponent models for this game
            cursor = conn.execute(
                """
                SELECT * FROM opponent_models WHERE game_id = ?
            """,
                (game_id,),
            )

            for row in cursor.fetchall():
                observer_name = row['observer_name']
                opponent_name = row['opponent_name']

                if observer_name not in models_dict:
                    models_dict[observer_name] = {}

                tendencies_json = (
                    row['tendencies_json'] if 'tendencies_json' in row.keys() else None
                )
                tendencies = None
                if tendencies_json:
                    try:
                        tendencies = json.loads(tendencies_json)
                    except json.JSONDecodeError:
                        tendencies = None

                if tendencies is None:
                    # Legacy rows only have derived rates. Preserve old load
                    # behavior, but prefer tendencies_json whenever available
                    # so counters and hands_dealt survive reloads exactly.
                    tendencies = {
                        'hands_observed': row['hands_observed'],
                        'vpip': row['vpip'],
                        'pfr': row['pfr'],
                        'aggression_factor': row['aggression_factor'],
                        'fold_to_cbet': row['fold_to_cbet'],
                        'bluff_frequency': row['bluff_frequency'],
                        'showdown_win_rate': row['showdown_win_rate'],
                        'recent_trend': row['recent_trend'] or 'stable',
                        # Counters - we can't restore these perfectly, but we can estimate
                        '_vpip_count': int(row['vpip'] * row['hands_observed']),
                        '_pfr_count': int(row['pfr'] * row['hands_observed']),
                        '_bet_raise_count': 0,  # Can't restore
                        '_call_count': 0,  # Can't restore
                        '_fold_to_cbet_count': 0,  # Can't restore
                        '_cbet_faced_count': 0,  # Can't restore
                        '_showdowns': 0,  # Can't restore
                        '_showdowns_won': 0,  # Can't restore
                    }

                # Restore narrative_observations from JSON-serialized notes column
                notes_json = row['notes'] if 'notes' in row.keys() else None
                narrative_observations = []
                if notes_json:
                    try:
                        narrative_observations = json.loads(notes_json)
                    except json.JSONDecodeError:
                        # Legacy format: plain text note
                        narrative_observations = [notes_json]

                # Pull v86 id columns if present (None on pre-v86 rows).
                row_keys = row.keys()
                observer_id = row['observer_id'] if 'observer_id' in row_keys else None
                opponent_id = row['opponent_id'] if 'opponent_id' in row_keys else None

                models_dict[observer_name][opponent_name] = {
                    'observer': observer_name,
                    'opponent': opponent_name,
                    'observer_id': observer_id,
                    'opponent_id': opponent_id,
                    'tendencies': tendencies,
                    'memorable_hands': [],
                    'narrative_observations': narrative_observations,
                }

            # Load memorable hands
            cursor = conn.execute(
                """
                SELECT * FROM memorable_hands WHERE game_id = ?
            """,
                (game_id,),
            )

            for row in cursor.fetchall():
                observer_name = row['observer_name']
                opponent_name = row['opponent_name']

                if observer_name in models_dict and opponent_name in models_dict[observer_name]:
                    models_dict[observer_name][opponent_name]['memorable_hands'].append(
                        {
                            'hand_id': row['hand_id'],
                            'memory_type': row['memory_type'],
                            'opponent_name': opponent_name,
                            'impact_score': row['impact_score'],
                            'narrative': row['narrative'] or '',
                            'hand_summary': '',  # Not stored in DB
                            'timestamp': row['created_at'] or datetime.now().isoformat(),
                        }
                    )

        if models_dict:
            logger.debug(f"Loaded opponent models for game {game_id}: {len(models_dict)} observers")

            # Rebuild the OpponentModelManager.__name_to_id__ sidecar
            # from the per-row ids we just loaded. This lets the
            # manager's registry pick up populated rows after a load
            # without requiring the column shape to round-trip a
            # separate table. Any name that appears with a non-None id
            # in any row contributes to the registry.
            name_to_id: Dict[str, Optional[str]] = {}
            for observer_name, opponents in models_dict.items():
                for opponent_name, model_data in opponents.items():
                    obs_id = model_data.get('observer_id')
                    opp_id = model_data.get('opponent_id')
                    if obs_id and observer_name not in name_to_id:
                        name_to_id[observer_name] = obs_id
                    if opp_id and opponent_name not in name_to_id:
                        name_to_id[opponent_name] = opp_id
            if name_to_id:
                models_dict['__name_to_id__'] = name_to_id

        return models_dict

    def load_cross_session_opponent_models(
        self, observer_name: str, user_id: str
    ) -> Dict[str, dict]:
        """Aggregate opponent stats across all games for this user.

        Combines data from all games where the observer has tracked opponents,
        using weighted averages based on hands_observed for numeric stats.

        Args:
            observer_name: The name of the player observing opponents (typically the human player)
            user_id: The owner ID to filter games by

        Returns:
            Dict mapping opponent_name -> {
                'session_count': int,       # Number of distinct games
                'total_hands': int,         # Sum of hands_observed across games
                'vpip': float,              # Weighted average
                'pfr': float,               # Weighted average
                'aggression_factor': float, # Weighted average
                'style': str,               # Style label based on aggregated stats
                'notes': List[str],         # Collected narrative observations
            }
        """
        if not user_id:
            return {}

        result: Dict[str, dict] = {}

        with self._get_connection() as conn:
            # Aggregate stats across all games for this user
            cursor = conn.execute(
                """
                SELECT
                    om.opponent_name,
                    COUNT(DISTINCT om.game_id) as session_count,
                    SUM(om.hands_observed) as total_hands,
                    SUM(om.vpip * om.hands_observed) as weighted_vpip,
                    SUM(om.pfr * om.hands_observed) as weighted_pfr,
                    SUM(om.aggression_factor * om.hands_observed) as weighted_aggression,
                    GROUP_CONCAT(om.notes, '|||') as all_notes
                FROM opponent_models om
                JOIN games g ON om.game_id = g.game_id
                WHERE om.observer_name = ?
                  AND g.owner_id = ?
                  AND om.hands_observed > 0
                GROUP BY om.opponent_name
            """,
                (observer_name, user_id),
            )

            for row in cursor.fetchall():
                opponent_name = row['opponent_name']
                total_hands = row['total_hands'] or 0
                session_count = row['session_count'] or 0

                if total_hands == 0:
                    continue

                # Calculate weighted averages
                vpip = (row['weighted_vpip'] or 0) / total_hands
                pfr = (row['weighted_pfr'] or 0) / total_hands
                aggression = (row['weighted_aggression'] or 0) / total_hands

                # Parse and deduplicate notes from all sessions
                all_notes_str = row['all_notes'] or ''
                notes = []
                if all_notes_str:
                    for notes_json in all_notes_str.split('|||'):
                        if notes_json and notes_json.strip():
                            try:
                                parsed = json.loads(notes_json)
                                if isinstance(parsed, list):
                                    for note in parsed:
                                        if note and note not in notes:
                                            notes.append(note)
                            except json.JSONDecodeError:
                                # Legacy format: plain text note
                                if notes_json not in notes:
                                    notes.append(notes_json)

                result[opponent_name] = {
                    'session_count': session_count,
                    'total_hands': total_hands,
                    'vpip': round(vpip, 3),
                    'pfr': round(pfr, 3),
                    'aggression_factor': round(aggression, 2),
                    'notes': notes[-10:],  # Keep up to 10 notes
                }

        if result:
            logger.debug(
                f"Loaded cross-session opponent models for {observer_name}: {len(result)} opponents"
            )

        return result

    def delete_opponent_models_for_game(self, game_id: str) -> None:
        """Delete all opponent models for a game."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM opponent_models WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM memorable_hands WHERE game_id = ?", (game_id,))
