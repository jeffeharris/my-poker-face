"""
Persistence layer for poker game using SQLite.
Handles saving and loading game states.
"""
import sqlite3
import json
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from poker.poker_game import PokerGameState, Player
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from core.card import Card
import logging

logger = logging.getLogger(__name__)

# Current schema version - increment when adding migrations
SCHEMA_VERSION = 19


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


class GamePersistence:
    """Handles persistence of poker games to SQLite database."""

    def __init__(self, db_path: str = "poker_games.db"):
        self.db_path = db_path
        self._init_db()
        self._run_migrations()
    
    def _serialize_card(self, card) -> Dict[str, Any]:
        """Ensure card is properly serialized."""
        if hasattr(card, 'to_dict'):
            return card.to_dict()
        elif isinstance(card, dict):
            # Validate dict has required fields
            if 'rank' in card and 'suit' in card:
                return card
            else:
                raise ValueError(f"Invalid card dict: missing rank or suit in {card}")
        else:
            raise ValueError(f"Unknown card format: {type(card)}")
    
    def _deserialize_card(self, card_data) -> Card:
        """Ensure card is properly deserialized to Card object."""
        if isinstance(card_data, dict):
            return Card.from_dict(card_data)
        elif hasattr(card_data, 'rank'):  # Already a Card object
            return card_data
        else:
            raise ValueError(f"Cannot deserialize card: {card_data}")
    
    def _serialize_cards(self, cards) -> List[Dict[str, Any]]:
        """Serialize a collection of cards."""
        if not cards:
            return []
        return [self._serialize_card(card) for card in cards]
    
    def _deserialize_cards(self, cards_data) -> tuple:
        """Deserialize a collection of cards."""
        if not cards_data:
            return tuple()
        return tuple(self._deserialize_card(card_data) for card_data in cards_data)
    
    def _init_db(self):
        """Initialize the database schema."""
        with sqlite3.connect(self.db_path) as conn:
            # Schema version tracking table - must be created first
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    description TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS games (
                    game_id TEXT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    phase TEXT NOT NULL,
                    num_players INTEGER NOT NULL,
                    pot_size REAL NOT NULL,
                    game_state_json TEXT NOT NULL,
                    owner_id TEXT,
                    owner_name TEXT
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS game_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    message_type TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_games_updated 
                ON games(updated_at DESC)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_game_id 
                ON game_messages(game_id, timestamp)
            """)
            
            # AI state persistence tables
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_player_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    conversation_history TEXT,  -- JSON array of messages
                    personality_state TEXT,     -- JSON of current personality modifiers
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    UNIQUE(game_id, player_name)
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS personality_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_name TEXT NOT NULL,
                    game_id TEXT NOT NULL,
                    hand_number INTEGER,
                    personality_traits TEXT,  -- JSON with all trait values
                    pressure_levels TEXT,     -- JSON with pressure per trait
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                )
            """)
            
            # Create indices for AI tables
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_player_game 
                ON ai_player_state(game_id, player_name)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_personality_snapshots 
                ON personality_snapshots(game_id, hand_number)
            """)
            
            # Pressure events tracking
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pressure_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    details_json TEXT,
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                )
            """)
            
            # Create indices for pressure events
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pressure_events_game 
                ON pressure_events(game_id)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pressure_events_player 
                ON pressure_events(player_name)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pressure_events_type 
                ON pressure_events(event_type)
            """)
            
            # Personality storage for AI-generated personalities
            conn.execute("""
                CREATE TABLE IF NOT EXISTS personalities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    config_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_generated BOOLEAN DEFAULT 1,
                    source TEXT DEFAULT 'ai_generated',
                    times_used INTEGER DEFAULT 0
                )
            """)

            # Hand history for AI memory and learning
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hand_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    hand_number INTEGER NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    players_json TEXT NOT NULL,
                    hole_cards_json TEXT,
                    community_cards_json TEXT,
                    actions_json TEXT NOT NULL,
                    winners_json TEXT,
                    pot_size INTEGER,
                    showdown BOOLEAN,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    UNIQUE(game_id, hand_number)
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_hand_history_game
                ON hand_history(game_id)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_hand_history_timestamp
                ON hand_history(timestamp DESC)
            """)

            # Opponent models for AI learning across sessions
            conn.execute("""
                CREATE TABLE IF NOT EXISTS opponent_models (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observer_name TEXT NOT NULL,
                    opponent_name TEXT NOT NULL,
                    hands_observed INTEGER DEFAULT 0,
                    vpip REAL DEFAULT 0.5,
                    pfr REAL DEFAULT 0.5,
                    aggression_factor REAL DEFAULT 1.0,
                    fold_to_cbet REAL DEFAULT 0.5,
                    bluff_frequency REAL DEFAULT 0.3,
                    showdown_win_rate REAL DEFAULT 0.5,
                    recent_trend TEXT,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(observer_name, opponent_name)
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_opponent_models_observer
                ON opponent_models(observer_name)
            """)

            # Memorable hands that AI players remember
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memorable_hands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observer_name TEXT NOT NULL,
                    opponent_name TEXT NOT NULL,
                    hand_id INTEGER NOT NULL,
                    memory_type TEXT NOT NULL,
                    impact_score REAL,
                    narrative TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (hand_id) REFERENCES hand_history(id)
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memorable_observer
                ON memorable_hands(observer_name)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memorable_opponent
                ON memorable_hands(opponent_name)
            """)
            
            # Add index for owner_id
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_games_owner
                ON games(owner_id)
            """)

    def _get_current_schema_version(self) -> int:
        """Get the current schema version from the database."""
        with sqlite3.connect(self.db_path) as conn:
            try:
                cursor = conn.execute("SELECT MAX(version) FROM schema_version")
                result = cursor.fetchone()[0]
                return result if result is not None else 0
            except sqlite3.OperationalError:
                # Table doesn't exist yet
                return 0

    def _run_migrations(self) -> None:
        """Run any pending schema migrations."""
        current_version = self._get_current_schema_version()

        if current_version >= SCHEMA_VERSION:
            return

        logger.info(f"Running database migrations from version {current_version} to {SCHEMA_VERSION}")

        migrations: Dict[int, tuple] = {
            1: (self._migrate_v1_add_owner_columns, "Add owner_id and owner_name to games table"),
            2: (self._migrate_v2_add_memory_tables, "Add AI memory and learning tables"),
            3: (self._migrate_v3_add_controller_state_tables, "Add emotional state and controller state tables"),
            4: (self._migrate_v4_add_tournament_tables, "Add tournament results and career stats tables"),
            5: (self._migrate_v5_add_avatar_images_table, "Add avatar_images table for storing character images"),
            6: (self._migrate_v6_add_api_usage_table, "Add api_usage table for LLM cost tracking"),
            7: (self._migrate_v7_add_reasoning_effort, "Add reasoning_effort column to api_usage table"),
            8: (self._migrate_v8_add_request_id, "Add request_id column for vendor correlation"),
            9: (self._migrate_v9_add_max_tokens, "Add max_tokens column for token limit tracking"),
            10: (self._migrate_v10_add_conversation_metrics, "Add message_count and system_prompt_length columns"),
            11: (self._migrate_v11_add_system_prompt_tokens, "Add system_prompt_tokens column for accurate token tracking"),
            12: (self._migrate_v12_drop_system_prompt_length, "Drop unused system_prompt_length column"),
            13: (self._migrate_v13_add_pricing_tables, "Add model_pricing table and estimated_cost column"),
            14: (self._migrate_v14_sku_based_pricing, "Redesign model_pricing as SKU-based rows"),
            15: (self._migrate_v15_add_pricing_validity_dates, "Add valid_from and valid_until to model_pricing"),
            16: (self._migrate_v16_add_pricing_id_to_usage, "Add pricing_id foreign key to api_usage"),
            17: (self._migrate_v17_consolidate_pricing_ids, "Replace 4 pricing_id columns with single JSON column"),
            18: (self._migrate_v18_add_prompt_captures, "Add prompt_captures table for debugging AI decisions"),
            19: (self._migrate_v19_add_conversation_history, "Add conversation_history column to prompt_captures"),
        }

        with sqlite3.connect(self.db_path) as conn:
            for version in range(current_version + 1, SCHEMA_VERSION + 1):
                if version in migrations:
                    migrate_func, description = migrations[version]
                    try:
                        migrate_func(conn)
                        conn.execute(
                            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                            (version, description)
                        )
                        conn.commit()
                        logger.info(f"Applied migration v{version}: {description}")
                    except Exception as e:
                        logger.error(f"Migration v{version} failed: {e}")
                        raise

    def _migrate_v1_add_owner_columns(self, conn: sqlite3.Connection) -> None:
        """Migration v1: Add owner_id and owner_name columns to games table."""
        cursor = conn.execute("PRAGMA table_info(games)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'owner_id' not in columns:
            conn.execute("ALTER TABLE games ADD COLUMN owner_id TEXT")
            conn.execute("ALTER TABLE games ADD COLUMN owner_name TEXT")
            # Purge old games without owners
            conn.execute("DELETE FROM games")
            logger.info("Added owner_id column and purged old games without owners")

    def _migrate_v2_add_memory_tables(self, conn: sqlite3.Connection) -> None:
        """Migration v2: Add AI memory and learning tables.

        These tables may already exist from _init_db, but this migration
        ensures the schema_version table tracks their addition.
        """
        # Verify tables exist (they should from _init_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?, ?)",
            ('hand_history', 'opponent_models', 'memorable_hands')
        )
        existing_tables = {row[0] for row in cursor.fetchall()}

        expected_tables = {'hand_history', 'opponent_models', 'memorable_hands'}
        missing_tables = expected_tables - existing_tables

        if missing_tables:
            logger.warning(f"Memory tables missing (will be created by _init_db): {missing_tables}")

        logger.info("AI memory tables verified/registered in schema version")

    def _migrate_v3_add_controller_state_tables(self, conn: sqlite3.Connection) -> None:
        """Migration v3: Add tables for emotional state and controller state persistence.

        This fixes the issue where TiltState and ElasticPersonality were lost on game reload.
        """
        # Emotional state table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS emotional_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL,
                player_name TEXT NOT NULL,
                valence REAL DEFAULT 0.0,
                arousal REAL DEFAULT 0.5,
                control REAL DEFAULT 0.5,
                focus REAL DEFAULT 0.5,
                narrative TEXT,
                inner_voice TEXT,
                generated_at_hand INTEGER DEFAULT 0,
                source_events TEXT,
                metadata_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (game_id) REFERENCES games(game_id),
                UNIQUE(game_id, player_name)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_emotional_state_game
            ON emotional_state(game_id, player_name)
        """)

        # Controller state table (for tilt and other controller-specific state)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS controller_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL,
                player_name TEXT NOT NULL,
                tilt_state_json TEXT,
                elastic_personality_json TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (game_id) REFERENCES games(game_id),
                UNIQUE(game_id, player_name)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_controller_state_game
            ON controller_state(game_id, player_name)
        """)

        logger.info("Created emotional_state and controller_state tables")

    def _migrate_v4_add_tournament_tables(self, conn: sqlite3.Connection) -> None:
        """Migration v4: Add tournament results and career stats tables."""
        # Tournament results - one row per completed game
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tournament_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL UNIQUE,
                winner_name TEXT,
                total_hands INTEGER DEFAULT 0,
                biggest_pot INTEGER DEFAULT 0,
                starting_player_count INTEGER,
                human_player_name TEXT,
                human_finishing_position INTEGER,
                started_at TIMESTAMP,
                ended_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (game_id) REFERENCES games(game_id)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tournament_results_winner
            ON tournament_results(winner_name)
        """)

        # Tournament standings - one row per player per tournament
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tournament_standings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL,
                player_name TEXT NOT NULL,
                is_human BOOLEAN DEFAULT 0,
                finishing_position INTEGER,
                eliminated_by TEXT,
                eliminated_at_hand INTEGER,
                FOREIGN KEY (game_id) REFERENCES games(game_id),
                UNIQUE(game_id, player_name)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tournament_standings_game
            ON tournament_standings(game_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tournament_standings_player
            ON tournament_standings(player_name)
        """)

        # Player career stats - human player only, aggregated across games
        conn.execute("""
            CREATE TABLE IF NOT EXISTS player_career_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_name TEXT NOT NULL UNIQUE,
                games_played INTEGER DEFAULT 0,
                games_won INTEGER DEFAULT 0,
                total_eliminations INTEGER DEFAULT 0,
                best_finish INTEGER,
                worst_finish INTEGER,
                avg_finish REAL,
                biggest_pot_ever INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_career_stats_player
            ON player_career_stats(player_name)
        """)

        logger.info("Created tournament_results, tournament_standings, and player_career_stats tables")

    def _migrate_v5_add_avatar_images_table(self, conn: sqlite3.Connection) -> None:
        """Migration v5: Add avatar_images table for storing character images in DB."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS avatar_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                personality_name TEXT NOT NULL,
                emotion TEXT NOT NULL,
                image_data BLOB NOT NULL,
                content_type TEXT DEFAULT 'image/png',
                width INTEGER DEFAULT 256,
                height INTEGER DEFAULT 256,
                file_size INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(personality_name, emotion)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_avatar_personality
            ON avatar_images(personality_name)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_avatar_emotion
            ON avatar_images(emotion)
        """)

        # Add elasticity_config column to personalities if missing
        cursor = conn.execute("PRAGMA table_info(personalities)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'elasticity_config' not in columns:
            conn.execute("ALTER TABLE personalities ADD COLUMN elasticity_config TEXT")

        logger.info("Created avatar_images table and verified personalities schema")

    def _migrate_v6_add_api_usage_table(self, conn: sqlite3.Connection) -> None:
        """Migration v6: Add api_usage table for LLM cost tracking."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_usage (
                id INTEGER PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                -- Context (nullable - not all calls have game context)
                game_id TEXT REFERENCES games(game_id) ON DELETE SET NULL,
                owner_id TEXT,
                player_name TEXT,
                hand_number INTEGER,

                -- Call classification (validated enum in code)
                call_type TEXT NOT NULL,
                prompt_template TEXT,

                -- Provider/Model
                provider TEXT NOT NULL,
                model TEXT NOT NULL,

                -- Token usage (for text completions)
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cached_tokens INTEGER DEFAULT 0,
                reasoning_tokens INTEGER DEFAULT 0,

                -- Image usage (for DALL-E - cost is per-image, not tokens)
                image_count INTEGER DEFAULT 0,
                image_size TEXT,

                -- Performance & Status
                latency_ms INTEGER,
                status TEXT NOT NULL,
                finish_reason TEXT,
                error_code TEXT
            )
        """)

        # Single-column indexes
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_owner
            ON api_usage(owner_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_game
            ON api_usage(game_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_created
            ON api_usage(created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_call_type
            ON api_usage(call_type)
        """)

        # Composite indexes for common cost queries
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_owner_created
            ON api_usage(owner_id, created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_owner_call_type
            ON api_usage(owner_id, call_type)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_game_call_type
            ON api_usage(game_id, call_type)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_model_created
            ON api_usage(model, created_at)
        """)

        logger.info("Created api_usage table for LLM cost tracking")

    def _migrate_v7_add_reasoning_effort(self, conn: sqlite3.Connection) -> None:
        """Migration v7: Add reasoning_effort column to api_usage table."""
        conn.execute("ALTER TABLE api_usage ADD COLUMN reasoning_effort TEXT")
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_model_effort
            ON api_usage(model, reasoning_effort)
        """)
        logger.info("Added reasoning_effort column to api_usage table")

    def _migrate_v8_add_request_id(self, conn: sqlite3.Connection) -> None:
        """Migration v8: Add request_id column for vendor API correlation."""
        conn.execute("ALTER TABLE api_usage ADD COLUMN request_id TEXT")
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_request_id
            ON api_usage(request_id)
        """)
        logger.info("Added request_id column to api_usage table")

    def _migrate_v9_add_max_tokens(self, conn: sqlite3.Connection) -> None:
        """Migration v9: Add max_tokens column for token limit tracking."""
        conn.execute("ALTER TABLE api_usage ADD COLUMN max_tokens INTEGER")
        logger.info("Added max_tokens column to api_usage table")

    def _migrate_v10_add_conversation_metrics(self, conn: sqlite3.Connection) -> None:
        """Migration v10: Add conversation metrics for cost analysis."""
        conn.execute("ALTER TABLE api_usage ADD COLUMN message_count INTEGER")
        conn.execute("ALTER TABLE api_usage ADD COLUMN system_prompt_length INTEGER")
        logger.info("Added message_count and system_prompt_length columns to api_usage table")

    def _migrate_v11_add_system_prompt_tokens(self, conn: sqlite3.Connection) -> None:
        """Migration v11: Add system_prompt_tokens for accurate token tracking."""
        conn.execute("ALTER TABLE api_usage ADD COLUMN system_prompt_tokens INTEGER")
        logger.info("Added system_prompt_tokens column to api_usage table")

    def _migrate_v12_drop_system_prompt_length(self, conn: sqlite3.Connection) -> None:
        """Migration v12: Drop unused system_prompt_length column."""
        conn.execute("ALTER TABLE api_usage DROP COLUMN system_prompt_length")
        logger.info("Dropped system_prompt_length column from api_usage table")

    def _migrate_v13_add_pricing_tables(self, conn: sqlite3.Connection) -> None:
        """Migration v13: Add model_pricing table and estimated_cost column."""
        # Create model_pricing table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_pricing (
                id INTEGER PRIMARY KEY,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                pricing_type TEXT NOT NULL,  -- 'text' or 'image'

                -- Text model pricing (per 1M tokens, in USD)
                input_price_per_million REAL,
                output_price_per_million REAL,
                cached_input_price_per_million REAL,

                -- Image model pricing (per image, in USD)
                image_size TEXT,
                image_price REAL,

                effective_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes TEXT,

                UNIQUE(provider, model, pricing_type, image_size)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_model_pricing_lookup
            ON model_pricing(provider, model, pricing_type)
        """)

        # Add estimated_cost column to api_usage
        conn.execute("ALTER TABLE api_usage ADD COLUMN estimated_cost REAL")

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_cost
            ON api_usage(estimated_cost)
        """)

        # Seed with current OpenAI pricing (as of Jan 2025)
        text_models = [
            # (provider, model, input, output, cached_input)
            ('openai', 'gpt-4o', 2.50, 10.00, 1.25),
            ('openai', 'gpt-4o-mini', 0.15, 0.60, 0.075),
            ('openai', 'gpt-4-turbo', 10.00, 30.00, 5.00),
            ('openai', 'gpt-4', 30.00, 60.00, 15.00),
            ('openai', 'gpt-3.5-turbo', 0.50, 1.50, 0.25),
            # Estimated pricing for newer models
            ('openai', 'gpt-5-nano', 0.20, 0.80, 0.10),
            ('openai', 'gpt-5-mini', 0.15, 0.60, 0.075),
        ]

        for provider, model, input_price, output_price, cached_price in text_models:
            conn.execute("""
                INSERT OR REPLACE INTO model_pricing
                (provider, model, pricing_type, input_price_per_million,
                 output_price_per_million, cached_input_price_per_million)
                VALUES (?, ?, 'text', ?, ?, ?)
            """, (provider, model, input_price, output_price, cached_price))

        # Image model pricing
        image_pricing = [
            # (provider, model, size, price)
            ('openai', 'dall-e-2', '256x256', 0.016),
            ('openai', 'dall-e-2', '512x512', 0.018),
            ('openai', 'dall-e-2', '1024x1024', 0.020),
            ('openai', 'dall-e-3', '1024x1024', 0.040),
            ('openai', 'dall-e-3', '1024x1792', 0.080),
            ('openai', 'dall-e-3', '1792x1024', 0.080),
        ]

        for provider, model, size, price in image_pricing:
            conn.execute("""
                INSERT OR REPLACE INTO model_pricing
                (provider, model, pricing_type, image_size, image_price)
                VALUES (?, ?, 'image', ?, ?)
            """, (provider, model, size, price))

        logger.info("Created model_pricing table and added estimated_cost column")

    def _migrate_v14_sku_based_pricing(self, conn: sqlite3.Connection) -> None:
        """Migration v14: Redesign model_pricing as SKU-based rows.

        Instead of one row per model with multiple price columns,
        each row is a SKU (provider, model, unit) with a single cost.

        Units include:
        - input_tokens_1m: Cost per 1M input tokens
        - output_tokens_1m: Cost per 1M output tokens
        - cached_input_tokens_1m: Cost per 1M cached input tokens
        - batch_input_tokens_1m: Cost per 1M batch input tokens (50% off)
        - batch_output_tokens_1m: Cost per 1M batch output tokens (50% off)
        - image_<size>: Cost per image at given size (e.g., image_1024x1024)
        """
        # Drop old table and create new schema
        conn.execute("DROP TABLE IF EXISTS model_pricing")

        conn.execute("""
            CREATE TABLE model_pricing (
                id INTEGER PRIMARY KEY,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                unit TEXT NOT NULL,
                cost REAL NOT NULL,
                effective_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes TEXT,
                UNIQUE(provider, model, unit)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_model_pricing_lookup
            ON model_pricing(provider, model)
        """)

        # Seed with OpenAI pricing (as of Jan 2025)
        # https://openai.com/api/pricing/
        skus = [
            # GPT-4o
            ('openai', 'gpt-4o', 'input_tokens_1m', 2.50),
            ('openai', 'gpt-4o', 'output_tokens_1m', 10.00),
            ('openai', 'gpt-4o', 'cached_input_tokens_1m', 1.25),
            ('openai', 'gpt-4o', 'batch_input_tokens_1m', 1.25),
            ('openai', 'gpt-4o', 'batch_output_tokens_1m', 5.00),

            # GPT-4o-mini
            ('openai', 'gpt-4o-mini', 'input_tokens_1m', 0.15),
            ('openai', 'gpt-4o-mini', 'output_tokens_1m', 0.60),
            ('openai', 'gpt-4o-mini', 'cached_input_tokens_1m', 0.075),
            ('openai', 'gpt-4o-mini', 'batch_input_tokens_1m', 0.075),
            ('openai', 'gpt-4o-mini', 'batch_output_tokens_1m', 0.30),

            # GPT-4-turbo
            ('openai', 'gpt-4-turbo', 'input_tokens_1m', 10.00),
            ('openai', 'gpt-4-turbo', 'output_tokens_1m', 30.00),

            # GPT-4
            ('openai', 'gpt-4', 'input_tokens_1m', 30.00),
            ('openai', 'gpt-4', 'output_tokens_1m', 60.00),

            # GPT-3.5-turbo
            ('openai', 'gpt-3.5-turbo', 'input_tokens_1m', 0.50),
            ('openai', 'gpt-3.5-turbo', 'output_tokens_1m', 1.50),

            # o1-preview (reasoning model)
            ('openai', 'o1-preview', 'input_tokens_1m', 15.00),
            ('openai', 'o1-preview', 'output_tokens_1m', 60.00),
            ('openai', 'o1-preview', 'cached_input_tokens_1m', 7.50),

            # o1-mini (reasoning model)
            ('openai', 'o1-mini', 'input_tokens_1m', 3.00),
            ('openai', 'o1-mini', 'output_tokens_1m', 12.00),
            ('openai', 'o1-mini', 'cached_input_tokens_1m', 1.50),

            # Estimated pricing for gpt-5 models (not official)
            ('openai', 'gpt-5-nano', 'input_tokens_1m', 0.20),
            ('openai', 'gpt-5-nano', 'output_tokens_1m', 0.80),
            ('openai', 'gpt-5-nano', 'cached_input_tokens_1m', 0.10),
            ('openai', 'gpt-5-mini', 'input_tokens_1m', 0.15),
            ('openai', 'gpt-5-mini', 'output_tokens_1m', 0.60),
            ('openai', 'gpt-5-mini', 'cached_input_tokens_1m', 0.075),

            # DALL-E 2
            ('openai', 'dall-e-2', 'image_256x256', 0.016),
            ('openai', 'dall-e-2', 'image_512x512', 0.018),
            ('openai', 'dall-e-2', 'image_1024x1024', 0.020),

            # DALL-E 3 Standard
            ('openai', 'dall-e-3', 'image_1024x1024', 0.040),
            ('openai', 'dall-e-3', 'image_1024x1792', 0.080),
            ('openai', 'dall-e-3', 'image_1792x1024', 0.080),

            # DALL-E 3 HD
            ('openai', 'dall-e-3', 'image_hd_1024x1024', 0.080),
            ('openai', 'dall-e-3', 'image_hd_1024x1792', 0.120),
            ('openai', 'dall-e-3', 'image_hd_1792x1024', 0.120),

            # Anthropic Claude 3.5 Sonnet
            ('anthropic', 'claude-3-5-sonnet-20241022', 'input_tokens_1m', 3.00),
            ('anthropic', 'claude-3-5-sonnet-20241022', 'output_tokens_1m', 15.00),
            ('anthropic', 'claude-3-5-sonnet-20241022', 'cached_input_tokens_1m', 0.30),

            # Anthropic Claude 3.5 Haiku
            ('anthropic', 'claude-3-5-haiku-20241022', 'input_tokens_1m', 0.80),
            ('anthropic', 'claude-3-5-haiku-20241022', 'output_tokens_1m', 4.00),
            ('anthropic', 'claude-3-5-haiku-20241022', 'cached_input_tokens_1m', 0.08),

            # Anthropic Claude 3 Opus
            ('anthropic', 'claude-3-opus-20240229', 'input_tokens_1m', 15.00),
            ('anthropic', 'claude-3-opus-20240229', 'output_tokens_1m', 75.00),
            ('anthropic', 'claude-3-opus-20240229', 'cached_input_tokens_1m', 1.50),
        ]

        for provider, model, unit, cost in skus:
            conn.execute("""
                INSERT INTO model_pricing (provider, model, unit, cost)
                VALUES (?, ?, ?, ?)
            """, (provider, model, unit, cost))

        logger.info("Redesigned model_pricing table with SKU-based rows")

    def _migrate_v15_add_pricing_validity_dates(self, conn: sqlite3.Connection) -> None:
        """Migration v15: Add valid_from and valid_until columns to model_pricing.

        This allows tracking pricing changes over time:
        - valid_from: When this price became effective (NULL = always valid)
        - valid_until: When this price expires (NULL = still current)

        Multiple price entries for the same SKU are allowed with different date ranges.
        """
        # Add validity columns
        conn.execute("ALTER TABLE model_pricing ADD COLUMN valid_from TIMESTAMP")
        conn.execute("ALTER TABLE model_pricing ADD COLUMN valid_until TIMESTAMP")

        # Drop old unique constraint and recreate with date ranges
        # SQLite doesn't support DROP CONSTRAINT, so we need to recreate the table
        conn.execute("""
            CREATE TABLE model_pricing_new (
                id INTEGER PRIMARY KEY,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                unit TEXT NOT NULL,
                cost REAL NOT NULL,
                valid_from TIMESTAMP,
                valid_until TIMESTAMP,
                effective_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes TEXT,
                UNIQUE(provider, model, unit, valid_from)
            )
        """)

        # Copy data from old table
        conn.execute("""
            INSERT INTO model_pricing_new (id, provider, model, unit, cost, effective_date, notes)
            SELECT id, provider, model, unit, cost, effective_date, notes FROM model_pricing
        """)

        # Drop old table and rename new one
        conn.execute("DROP TABLE model_pricing")
        conn.execute("ALTER TABLE model_pricing_new RENAME TO model_pricing")

        # Recreate index
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_model_pricing_lookup
            ON model_pricing(provider, model)
        """)

        # Add index for date-based lookups
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_model_pricing_validity
            ON model_pricing(provider, model, unit, valid_from, valid_until)
        """)

        logger.info("Added valid_from and valid_until columns to model_pricing")

    def _migrate_v16_add_pricing_id_to_usage(self, conn: sqlite3.Connection) -> None:
        """Migration v16: Add pricing_ids JSON column to api_usage for audit trail.

        Stores references to which model_pricing rows were used for cost calculation.
        JSON format: {"input": 1, "output": 2, "cached": 3} or {"image": 45}
        """
        conn.execute("ALTER TABLE api_usage ADD COLUMN pricing_ids TEXT")

        logger.info("Added pricing_ids column to api_usage table")

    def _migrate_v17_consolidate_pricing_ids(self, conn: sqlite3.Connection) -> None:
        """Migration v17: Consolidate 4 pricing_id columns into single JSON column.

        This fixes v16 which may have created separate columns instead of JSON.
        SQLite doesn't support DROP COLUMN in older versions, so we recreate the table.
        """
        # Check if we need to migrate (4 columns exist instead of pricing_ids)
        cursor = conn.execute("PRAGMA table_info(api_usage)")
        columns = {row[1] for row in cursor}

        if 'input_pricing_id' in columns:
            # Old schema - need to migrate
            # Create new table without the 4 pricing_id columns, with pricing_ids JSON
            conn.execute("""
                CREATE TABLE api_usage_new (
                    id INTEGER PRIMARY KEY,
                    created_at TIMESTAMP,
                    game_id TEXT,
                    owner_id TEXT,
                    player_name TEXT,
                    hand_number INTEGER,
                    call_type TEXT,
                    prompt_template TEXT,
                    provider TEXT,
                    model TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    cached_tokens INTEGER,
                    reasoning_tokens INTEGER,
                    image_count INTEGER,
                    image_size TEXT,
                    latency_ms INTEGER,
                    status TEXT,
                    finish_reason TEXT,
                    error_code TEXT,
                    reasoning_effort TEXT,
                    request_id TEXT,
                    max_tokens INTEGER,
                    message_count INTEGER,
                    system_prompt_tokens INTEGER,
                    estimated_cost REAL,
                    pricing_ids TEXT
                )
            """)

            # Copy data, converting old columns to JSON
            conn.execute("""
                INSERT INTO api_usage_new
                SELECT
                    id, created_at, game_id, owner_id, player_name, hand_number,
                    call_type, prompt_template, provider, model,
                    input_tokens, output_tokens, cached_tokens, reasoning_tokens,
                    image_count, image_size, latency_ms, status, finish_reason,
                    error_code, reasoning_effort, request_id, max_tokens,
                    message_count, system_prompt_tokens, estimated_cost,
                    CASE
                        WHEN image_pricing_id IS NOT NULL THEN json_object('image', image_pricing_id)
                        WHEN input_pricing_id IS NOT NULL THEN
                            CASE
                                WHEN cached_pricing_id IS NOT NULL THEN
                                    json_object('input', input_pricing_id, 'output', output_pricing_id, 'cached', cached_pricing_id)
                                ELSE
                                    json_object('input', input_pricing_id, 'output', output_pricing_id)
                            END
                        ELSE NULL
                    END
                FROM api_usage
            """)

            conn.execute("DROP TABLE api_usage")
            conn.execute("ALTER TABLE api_usage_new RENAME TO api_usage")

            # Recreate indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_game ON api_usage(game_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_created ON api_usage(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_call_type ON api_usage(call_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_cost ON api_usage(estimated_cost)")

            logger.info("Consolidated 4 pricing_id columns into pricing_ids JSON")
        elif 'pricing_ids' not in columns:
            # Neither schema exists - add the column
            conn.execute("ALTER TABLE api_usage ADD COLUMN pricing_ids TEXT")
            logger.info("Added pricing_ids column to api_usage table")
        else:
            logger.info("pricing_ids column already exists, no migration needed")

    def _migrate_v18_add_prompt_captures(self, conn: sqlite3.Connection) -> None:
        """Migration v18: Add prompt_captures table for debugging AI decisions.

        This table stores full prompts and responses for AI player decisions,
        enabling analysis and replay of AI behavior.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prompt_captures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                game_id TEXT NOT NULL,
                player_name TEXT NOT NULL,
                hand_number INTEGER,
                phase TEXT NOT NULL,
                action_taken TEXT,
                system_prompt TEXT NOT NULL,
                user_message TEXT NOT NULL,
                ai_response TEXT NOT NULL,
                pot_total INTEGER,
                cost_to_call INTEGER,
                pot_odds REAL,
                player_stack INTEGER,
                community_cards TEXT,
                player_hand TEXT,
                valid_actions TEXT,
                raise_amount INTEGER,
                model TEXT,
                latency_ms INTEGER,
                input_tokens INTEGER,
                output_tokens INTEGER,
                tags TEXT,
                notes TEXT,
                FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE CASCADE
            )
        """)

        # Create indexes for efficient querying
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_game ON prompt_captures(game_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_player ON prompt_captures(player_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_action ON prompt_captures(action_taken)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_pot_odds ON prompt_captures(pot_odds)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_created ON prompt_captures(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_phase ON prompt_captures(phase)")

        logger.info("Created prompt_captures table for AI decision debugging")

    def _migrate_v19_add_conversation_history(self, conn: sqlite3.Connection) -> None:
        """Migration v19: Add conversation_history column to prompt_captures.

        This stores the full conversation history (prior messages) that were
        sent to the LLM, which affects the AI's decision.
        """
        cursor = conn.execute("PRAGMA table_info(prompt_captures)")
        columns = {row[1] for row in cursor}

        if 'conversation_history' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN conversation_history TEXT")
            logger.info("Added conversation_history column to prompt_captures")

        # Also add raw_api_response column
        cursor = conn.execute("PRAGMA table_info(prompt_captures)")
        columns = {row[1] for row in cursor}
        if 'raw_api_response' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN raw_api_response TEXT")
            logger.info("Added raw_api_response column to prompt_captures")

    def save_game(self, game_id: str, state_machine: PokerStateMachine, 
                  owner_id: Optional[str] = None, owner_name: Optional[str] = None) -> None:
        """Save a game state to the database."""
        game_state = state_machine.game_state
        
        # Convert game state to dict and then to JSON
        state_dict = self._prepare_state_for_save(game_state)
        state_dict['current_phase'] = state_machine.current_phase.value
        
        game_json = json.dumps(state_dict)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO games 
                (game_id, updated_at, phase, num_players, pot_size, game_state_json, owner_id, owner_name)
                VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?)
            """, (
                game_id,
                state_machine.current_phase.value,
                len(game_state.players),
                game_state.pot['total'],
                game_json,
                owner_id,
                owner_name
            ))
    
    def load_game(self, game_id: str) -> Optional[PokerStateMachine]:
        """Load a game state from the database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM games WHERE game_id = ?", 
                (game_id,)
            )
            row = cursor.fetchone()
            
            if not row:
                return None
            
            # Parse the JSON and recreate the game state
            state_dict = json.loads(row['game_state_json'])
            game_state = self._restore_state_from_dict(state_dict)

            # Restore the phase - handle both int and string values
            try:
                phase_value = state_dict.get('current_phase', 0)
                if isinstance(phase_value, str):
                    phase_value = int(phase_value)
                phase = PokerPhase(phase_value)
            except (ValueError, KeyError):
                print(f"Warning: Could not restore phase {state_dict.get('current_phase')}, using INITIALIZING_HAND")
                phase = PokerPhase.INITIALIZING_HAND

            # Create state machine with the loaded state and phase
            return PokerStateMachine.from_saved_state(game_state, phase)
    
    def list_games(self, owner_id: Optional[str] = None, limit: int = 20) -> List[SavedGame]:
        """List saved games, most recently updated first. Filter by owner_id if provided."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            if owner_id:
                cursor = conn.execute("""
                    SELECT * FROM games 
                    WHERE owner_id = ?
                    ORDER BY updated_at DESC 
                    LIMIT ?
                """, (owner_id, limit))
            else:
                cursor = conn.execute("""
                    SELECT * FROM games 
                    ORDER BY updated_at DESC 
                    LIMIT ?
                """, (limit,))
            
            games = []
            for row in cursor:
                games.append(SavedGame(
                    game_id=row['game_id'],
                    created_at=datetime.fromisoformat(row['created_at']),
                    updated_at=datetime.fromisoformat(row['updated_at']),
                    phase=row['phase'],
                    num_players=row['num_players'],
                    pot_size=row['pot_size'],
                    game_state_json=row['game_state_json'],
                    owner_id=row['owner_id'],
                    owner_name=row['owner_name']
                ))
            
            return games
    
    def count_user_games(self, owner_id: str) -> int:
        """Count how many games a user owns."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT COUNT(*) FROM games WHERE owner_id = ?
            """, (owner_id,))
            return cursor.fetchone()[0]
    
    def delete_game(self, game_id: str) -> None:
        """Delete a game and all associated data."""
        with sqlite3.connect(self.db_path) as conn:
            # Delete all associated data (order matters for foreign keys)
            conn.execute("DELETE FROM personality_snapshots WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM ai_player_state WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM game_messages WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM games WHERE game_id = ?", (game_id,))
    
    def save_message(self, game_id: str, message_type: str, message_text: str) -> None:
        """Save a game message/event."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO game_messages (game_id, message_type, message_text)
                VALUES (?, ?, ?)
            """, (game_id, message_type, message_text))
    
    def load_messages(self, game_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Load recent messages for a game."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM game_messages
                WHERE game_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (game_id, limit))

            messages = []
            for row in cursor:
                # Parse "sender: content" format back into separate fields
                text = row['message_text']
                if ': ' in text:
                    sender, content = text.split(': ', 1)
                else:
                    sender = 'System'
                    content = text
                messages.append({
                    'id': str(row['id']),
                    'timestamp': row['timestamp'],
                    'type': row['message_type'],
                    'sender': sender,
                    'content': content
                })

            return list(reversed(messages))  # Return in chronological order
    
    def _prepare_state_for_save(self, game_state: PokerGameState) -> Dict[str, Any]:
        """Prepare game state for JSON serialization."""
        state_dict = game_state.to_dict()
        
        # The to_dict() method already handles most serialization,
        # but we need to ensure all custom objects are properly converted
        return state_dict
    
    def _restore_state_from_dict(self, state_dict: Dict[str, Any]) -> PokerGameState:
        """Restore game state from dictionary."""
        # Reconstruct players
        players = []
        for player_data in state_dict['players']:
            # Reconstruct hand if present
            hand = None
            if player_data.get('hand'):
                try:
                    hand = self._deserialize_cards(player_data['hand'])
                except Exception as e:
                    logger.warning(f"Error deserializing hand for {player_data['name']}: {e}")
                    hand = None
            
            player = Player(
                name=player_data['name'],
                stack=player_data['stack'],
                is_human=player_data['is_human'],
                bet=player_data['bet'],
                hand=hand,
                is_all_in=player_data['is_all_in'],
                is_folded=player_data['is_folded'],
                has_acted=player_data['has_acted']
            )
            players.append(player)
        
        # Reconstruct deck
        try:
            deck = self._deserialize_cards(state_dict.get('deck', []))
        except Exception as e:
            logger.warning(f"Error deserializing deck: {e}")
            deck = tuple()
        
        # Reconstruct discard pile
        try:
            discard_pile = self._deserialize_cards(state_dict.get('discard_pile', []))
        except Exception as e:
            logger.warning(f"Error deserializing discard pile: {e}")
            discard_pile = tuple()
        
        # Reconstruct community cards
        try:
            community_cards = self._deserialize_cards(state_dict.get('community_cards', []))
        except Exception as e:
            logger.warning(f"Error deserializing community cards: {e}")
            community_cards = tuple()
        
        # Create the game state
        return PokerGameState(
            players=tuple(players),
            deck=deck,
            discard_pile=discard_pile,
            pot=state_dict['pot'],
            current_player_idx=state_dict['current_player_idx'],
            current_dealer_idx=state_dict['current_dealer_idx'],
            community_cards=community_cards,
            current_ante=state_dict['current_ante'],
            pre_flop_action_taken=state_dict['pre_flop_action_taken'],
            awaiting_action=state_dict['awaiting_action']
        )
    
    # AI State Persistence Methods
    def save_ai_player_state(self, game_id: str, player_name: str, 
                            messages: List[Dict[str, str]], 
                            personality_state: Dict[str, Any]) -> None:
        """Save AI player conversation history and personality state."""
        with sqlite3.connect(self.db_path) as conn:
            conversation_history = json.dumps(messages)
            personality_json = json.dumps(personality_state)
            
            conn.execute("""
                INSERT OR REPLACE INTO ai_player_state
                (game_id, player_name, conversation_history, personality_state)
                VALUES (?, ?, ?, ?)
            """, (game_id, player_name, conversation_history, personality_json))
    
    def load_ai_player_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all AI player states for a game."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT player_name, conversation_history, personality_state
                FROM ai_player_state
                WHERE game_id = ?
            """, (game_id,))
            
            ai_states = {}
            for row in cursor.fetchall():
                ai_states[row['player_name']] = {
                    'messages': json.loads(row['conversation_history']),
                    'personality_state': json.loads(row['personality_state'])
                }
            
            return ai_states
    
    def save_personality_snapshot(self, game_id: str, player_name: str, 
                                 hand_number: int, traits: Dict[str, Any], 
                                 pressure_levels: Optional[Dict[str, float]] = None) -> None:
        """Save a snapshot of personality state for elasticity tracking."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO personality_snapshots
                (player_name, game_id, hand_number, personality_traits, pressure_levels)
                VALUES (?, ?, ?, ?, ?)
            """, (
                player_name,
                game_id,
                hand_number,
                json.dumps(traits),
                json.dumps(pressure_levels or {})
            ))
    
    def save_personality(self, name: str, config: Dict[str, Any], source: str = 'ai_generated') -> None:
        """Save a personality configuration to the database."""
        # Extract elasticity_config if present in the main config
        elasticity_config = config.get('elasticity_config', {})
        
        # Remove elasticity_config from main config if it exists (to store separately)
        config_without_elasticity = {k: v for k, v in config.items() if k != 'elasticity_config'}
        
        with sqlite3.connect(self.db_path) as conn:
            # Check if elasticity_config column exists
            cursor = conn.execute("PRAGMA table_info(personalities)")
            columns = [row[1] for row in cursor.fetchall()]
            
            if 'elasticity_config' in columns:
                conn.execute("""
                    INSERT OR REPLACE INTO personalities
                    (name, config_json, elasticity_config, source, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (name, json.dumps(config_without_elasticity), json.dumps(elasticity_config), source))
            else:
                # Fallback for old schema
                conn.execute("""
                    INSERT OR REPLACE INTO personalities
                    (name, config_json, source, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """, (name, json.dumps(config), source))
    
    def load_personality(self, name: str) -> Optional[Dict[str, Any]]:
        """Load a personality configuration from the database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            # Check if elasticity_config column exists
            cursor = conn.execute("PRAGMA table_info(personalities)")
            columns = [row[1] for row in cursor.fetchall()]
            
            if 'elasticity_config' in columns:
                cursor = conn.execute("""
                    SELECT config_json, elasticity_config FROM personalities
                    WHERE name = ?
                """, (name,))
            else:
                cursor = conn.execute("""
                    SELECT config_json FROM personalities
                    WHERE name = ?
                """, (name,))
            
            row = cursor.fetchone()
            if row:
                # Increment usage counter
                conn.execute("""
                    UPDATE personalities 
                    SET times_used = times_used + 1 
                    WHERE name = ?
                """, (name,))
                
                config = json.loads(row['config_json'])
                
                # Add elasticity_config if available
                if 'elasticity_config' in columns and row['elasticity_config']:
                    config['elasticity_config'] = json.loads(row['elasticity_config'])
                
                return config
            
            return None
    
    def increment_personality_usage(self, name: str) -> None:
        """Increment the usage counter for a personality."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE personalities 
                SET times_used = times_used + 1 
                WHERE name = ?
            """, (name,))
    
    def list_personalities(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List all personalities with metadata."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT name, source, created_at, updated_at, times_used, is_generated
                FROM personalities
                ORDER BY times_used DESC, updated_at DESC
                LIMIT ?
            """, (limit,))
            
            personalities = []
            for row in cursor:
                personalities.append({
                    'name': row['name'],
                    'source': row['source'],
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at'],
                    'times_used': row['times_used'],
                    'is_generated': bool(row['is_generated'])
                })
            
            return personalities
    
    def delete_personality(self, name: str) -> bool:
        """Delete a personality from the database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM personalities WHERE name = ?",
                    (name,)
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting personality {name}: {e}")
            return False

    # Emotional State Persistence Methods
    def save_emotional_state(self, game_id: str, player_name: str,
                             emotional_state) -> None:
        """Save emotional state for a player.

        Args:
            game_id: The game identifier
            player_name: The player's name
            emotional_state: EmotionalState object or dict from EmotionalState.to_dict()
        """
        # Convert to dict if it's an EmotionalState object
        if hasattr(emotional_state, 'to_dict'):
            state_dict = emotional_state.to_dict()
        else:
            state_dict = emotional_state

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO emotional_state
                (game_id, player_name, valence, arousal, control, focus,
                 narrative, inner_voice, generated_at_hand, source_events,
                 metadata_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                game_id,
                player_name,
                state_dict.get('valence', 0.0),
                state_dict.get('arousal', 0.5),
                state_dict.get('control', 0.5),
                state_dict.get('focus', 0.5),
                state_dict.get('narrative', ''),
                state_dict.get('inner_voice', ''),
                state_dict.get('generated_at_hand', 0),
                json.dumps(state_dict.get('source_events', [])),
                json.dumps({
                    'created_at': state_dict.get('created_at'),
                    'used_fallback': state_dict.get('used_fallback', False)
                })
            ))

    def load_emotional_state(self, game_id: str, player_name: str) -> Optional[Dict[str, Any]]:
        """Load emotional state for a player.

        Returns:
            Dict suitable for EmotionalState.from_dict(), or None if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM emotional_state
                WHERE game_id = ? AND player_name = ?
            """, (game_id, player_name))

            row = cursor.fetchone()
            if not row:
                return None

            metadata = json.loads(row['metadata_json']) if row['metadata_json'] else {}

            return {
                'valence': row['valence'],
                'arousal': row['arousal'],
                'control': row['control'],
                'focus': row['focus'],
                'narrative': row['narrative'] or '',
                'inner_voice': row['inner_voice'] or '',
                'generated_at_hand': row['generated_at_hand'],
                'source_events': json.loads(row['source_events']) if row['source_events'] else [],
                'created_at': metadata.get('created_at'),
                'used_fallback': metadata.get('used_fallback', False)
            }

    def load_all_emotional_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all emotional states for a game.

        Returns:
            Dict mapping player_name -> emotional_state dict
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM emotional_state
                WHERE game_id = ?
            """, (game_id,))

            states = {}
            for row in cursor.fetchall():
                metadata = json.loads(row['metadata_json']) if row['metadata_json'] else {}
                states[row['player_name']] = {
                    'valence': row['valence'],
                    'arousal': row['arousal'],
                    'control': row['control'],
                    'focus': row['focus'],
                    'narrative': row['narrative'] or '',
                    'inner_voice': row['inner_voice'] or '',
                    'generated_at_hand': row['generated_at_hand'],
                    'source_events': json.loads(row['source_events']) if row['source_events'] else [],
                    'created_at': metadata.get('created_at'),
                    'used_fallback': metadata.get('used_fallback', False)
                }

            return states

    # Controller State Persistence Methods (Tilt + ElasticPersonality)
    def save_controller_state(self, game_id: str, player_name: str,
                              psychology: Dict[str, Any]) -> None:
        """Save unified psychology state for a player.

        Args:
            game_id: The game identifier
            player_name: The player's name
            psychology: Dict from PlayerPsychology.to_dict()
        """
        # Extract components from unified psychology
        tilt_state = psychology.get('tilt')
        elastic_personality = psychology.get('elastic')

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO controller_state
                (game_id, player_name, tilt_state_json, elastic_personality_json, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                game_id,
                player_name,
                json.dumps(tilt_state) if tilt_state else None,
                json.dumps(elastic_personality) if elastic_personality else None
            ))

    def load_controller_state(self, game_id: str, player_name: str) -> Optional[Dict[str, Any]]:
        """Load controller state for a player.

        Returns:
            Dict with 'tilt_state' and 'elastic_personality' keys, or None if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT tilt_state_json, elastic_personality_json
                FROM controller_state
                WHERE game_id = ? AND player_name = ?
            """, (game_id, player_name))

            row = cursor.fetchone()
            if not row:
                return None

            return {
                'tilt_state': json.loads(row['tilt_state_json']) if row['tilt_state_json'] else None,
                'elastic_personality': json.loads(row['elastic_personality_json']) if row['elastic_personality_json'] else None
            }

    def load_all_controller_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all controller states for a game.

        Returns:
            Dict mapping player_name -> controller state dict
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT player_name, tilt_state_json, elastic_personality_json
                FROM controller_state
                WHERE game_id = ?
            """, (game_id,))

            states = {}
            for row in cursor.fetchall():
                states[row['player_name']] = {
                    'tilt_state': json.loads(row['tilt_state_json']) if row['tilt_state_json'] else None,
                    'elastic_personality': json.loads(row['elastic_personality_json']) if row['elastic_personality_json'] else None
                }

            return states

    def delete_emotional_state_for_game(self, game_id: str) -> None:
        """Delete all emotional states for a game."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM emotional_state WHERE game_id = ?", (game_id,))

    def delete_controller_state_for_game(self, game_id: str) -> None:
        """Delete all controller states for a game."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM controller_state WHERE game_id = ?", (game_id,))

    # Tournament Results Persistence Methods
    def save_tournament_result(self, game_id: str, result: Dict[str, Any]) -> None:
        """Save tournament result when game completes.

        Args:
            game_id: The game identifier
            result: Dict with keys: winner_name, total_hands, biggest_pot,
                   starting_player_count, human_player_name, human_finishing_position,
                   started_at, standings (list of player standings)
        """
        with sqlite3.connect(self.db_path) as conn:
            # Save main tournament result
            conn.execute("""
                INSERT OR REPLACE INTO tournament_results
                (game_id, winner_name, total_hands, biggest_pot, starting_player_count,
                 human_player_name, human_finishing_position, started_at, ended_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                game_id,
                result.get('winner_name'),
                result.get('total_hands', 0),
                result.get('biggest_pot', 0),
                result.get('starting_player_count'),
                result.get('human_player_name'),
                result.get('human_finishing_position'),
                result.get('started_at')
            ))

            # Save individual standings
            standings = result.get('standings', [])
            for standing in standings:
                conn.execute("""
                    INSERT OR REPLACE INTO tournament_standings
                    (game_id, player_name, is_human, finishing_position,
                     eliminated_by, eliminated_at_hand)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    game_id,
                    standing.get('player_name'),
                    standing.get('is_human', False),
                    standing.get('finishing_position'),
                    standing.get('eliminated_by'),
                    standing.get('eliminated_at_hand')
                ))

    def get_tournament_result(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Load tournament result for a completed game."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Get main result
            cursor = conn.execute("""
                SELECT * FROM tournament_results WHERE game_id = ?
            """, (game_id,))
            row = cursor.fetchone()

            if not row:
                return None

            # Get standings
            standings_cursor = conn.execute("""
                SELECT * FROM tournament_standings
                WHERE game_id = ?
                ORDER BY finishing_position ASC
            """, (game_id,))

            standings = []
            for s_row in standings_cursor.fetchall():
                standings.append({
                    'player_name': s_row['player_name'],
                    'is_human': bool(s_row['is_human']),
                    'finishing_position': s_row['finishing_position'],
                    'eliminated_by': s_row['eliminated_by'],
                    'eliminated_at_hand': s_row['eliminated_at_hand']
                })

            return {
                'game_id': row['game_id'],
                'winner_name': row['winner_name'],
                'total_hands': row['total_hands'],
                'biggest_pot': row['biggest_pot'],
                'starting_player_count': row['starting_player_count'],
                'human_player_name': row['human_player_name'],
                'human_finishing_position': row['human_finishing_position'],
                'started_at': row['started_at'],
                'ended_at': row['ended_at'],
                'standings': standings
            }

    def update_career_stats(self, player_name: str, tournament_result: Dict[str, Any]) -> None:
        """Update career stats for a player after a tournament.

        Args:
            player_name: The human player's name
            tournament_result: Dict with tournament result data
        """
        # Find the player's standing in this tournament
        standings = tournament_result.get('standings', [])
        player_standing = next(
            (s for s in standings if s.get('player_name') == player_name),
            None
        )

        if not player_standing:
            logger.warning(f"Player {player_name} not found in tournament standings")
            return

        finishing_position = player_standing.get('finishing_position', 0)
        is_winner = finishing_position == 1

        # Count eliminations by this player
        eliminations_this_game = sum(
            1 for s in standings
            if s.get('eliminated_by') == player_name
        )

        biggest_pot = tournament_result.get('biggest_pot', 0)

        with sqlite3.connect(self.db_path) as conn:
            # Check if player exists
            cursor = conn.execute("""
                SELECT * FROM player_career_stats WHERE player_name = ?
            """, (player_name,))
            existing = cursor.fetchone()

            if existing:
                # Update existing stats
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT * FROM player_career_stats WHERE player_name = ?
                """, (player_name,))
                row = cursor.fetchone()

                games_played = row['games_played'] + 1
                games_won = row['games_won'] + (1 if is_winner else 0)
                total_eliminations = row['total_eliminations'] + eliminations_this_game

                # Update best/worst finish
                best_finish = row['best_finish']
                if best_finish is None or finishing_position < best_finish:
                    best_finish = finishing_position

                worst_finish = row['worst_finish']
                if worst_finish is None or finishing_position > worst_finish:
                    worst_finish = finishing_position

                # Calculate new average
                old_avg = row['avg_finish'] or finishing_position
                avg_finish = ((old_avg * (games_played - 1)) + finishing_position) / games_played

                # Update biggest pot
                biggest_pot_ever = max(row['biggest_pot_ever'] or 0, biggest_pot)

                conn.execute("""
                    UPDATE player_career_stats
                    SET games_played = ?,
                        games_won = ?,
                        total_eliminations = ?,
                        best_finish = ?,
                        worst_finish = ?,
                        avg_finish = ?,
                        biggest_pot_ever = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE player_name = ?
                """, (
                    games_played, games_won, total_eliminations,
                    best_finish, worst_finish, avg_finish, biggest_pot_ever,
                    player_name
                ))
            else:
                # Insert new player
                conn.execute("""
                    INSERT INTO player_career_stats
                    (player_name, games_played, games_won, total_eliminations,
                     best_finish, worst_finish, avg_finish, biggest_pot_ever)
                    VALUES (?, 1, ?, ?, ?, ?, ?, ?)
                """, (
                    player_name,
                    1 if is_winner else 0,
                    eliminations_this_game,
                    finishing_position,
                    finishing_position,
                    float(finishing_position),
                    biggest_pot
                ))

    def get_career_stats(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Get career stats for a player."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM player_career_stats WHERE player_name = ?
            """, (player_name,))
            row = cursor.fetchone()

            if not row:
                return None

            return {
                'player_name': row['player_name'],
                'games_played': row['games_played'],
                'games_won': row['games_won'],
                'total_eliminations': row['total_eliminations'],
                'best_finish': row['best_finish'],
                'worst_finish': row['worst_finish'],
                'avg_finish': row['avg_finish'],
                'biggest_pot_ever': row['biggest_pot_ever'],
                'win_rate': row['games_won'] / row['games_played'] if row['games_played'] > 0 else 0
            }

    def get_tournament_history(self, player_name: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get tournament history for a player."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT tr.*, ts.finishing_position, ts.eliminated_by
                FROM tournament_results tr
                JOIN tournament_standings ts ON tr.game_id = ts.game_id
                WHERE ts.player_name = ?
                ORDER BY tr.ended_at DESC
                LIMIT ?
            """, (player_name, limit))

            history = []
            for row in cursor.fetchall():
                history.append({
                    'game_id': row['game_id'],
                    'winner_name': row['winner_name'],
                    'total_hands': row['total_hands'],
                    'biggest_pot': row['biggest_pot'],
                    'player_count': row['starting_player_count'],
                    'your_position': row['finishing_position'],
                    'eliminated_by': row['eliminated_by'],
                    'ended_at': row['ended_at']
                })

            return history

    def get_eliminated_personalities(self, player_name: str) -> List[Dict[str, Any]]:
        """Get all unique personalities eliminated by this player across all games.

        Returns a list of personalities with the first time they were eliminated.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Get unique personalities eliminated by this player, with first elimination date
            cursor = conn.execute("""
                SELECT
                    ts.player_name as personality_name,
                    MIN(tr.ended_at) as first_eliminated_at,
                    COUNT(*) as times_eliminated
                FROM tournament_standings ts
                JOIN tournament_results tr ON ts.game_id = tr.game_id
                WHERE ts.eliminated_by = ? AND ts.is_human = 0
                GROUP BY ts.player_name
                ORDER BY MIN(tr.ended_at) ASC
            """, (player_name,))

            personalities = []
            for row in cursor.fetchall():
                personalities.append({
                    'name': row['personality_name'],
                    'first_eliminated_at': row['first_eliminated_at'],
                    'times_eliminated': row['times_eliminated']
                })

            return personalities

    # Avatar Image Persistence Methods
    def save_avatar_image(self, personality_name: str, emotion: str,
                          image_data: bytes, width: int = 256, height: int = 256,
                          content_type: str = 'image/png') -> None:
        """Save an avatar image to the database.

        Args:
            personality_name: The personality name (e.g., "Bob Ross")
            emotion: The emotion (confident, happy, thinking, nervous, angry, shocked)
            image_data: The PNG image bytes
            width: Image width (default 256)
            height: Image height (default 256)
            content_type: MIME type (default image/png)
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO avatar_images
                (personality_name, emotion, image_data, content_type, width, height, file_size, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                personality_name,
                emotion,
                image_data,
                content_type,
                width,
                height,
                len(image_data)
            ))

    def load_avatar_image(self, personality_name: str, emotion: str) -> Optional[bytes]:
        """Load avatar image data from database.

        Args:
            personality_name: The personality name
            emotion: The emotion

        Returns:
            Image bytes if found, None otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT image_data FROM avatar_images
                WHERE personality_name = ? AND emotion = ?
            """, (personality_name, emotion))

            row = cursor.fetchone()
            return row[0] if row else None

    def load_avatar_image_with_metadata(self, personality_name: str, emotion: str) -> Optional[Dict[str, Any]]:
        """Load avatar image with metadata from database.

        Returns:
            Dict with image_data, content_type, width, height, file_size or None
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT image_data, content_type, width, height, file_size
                FROM avatar_images
                WHERE personality_name = ? AND emotion = ?
            """, (personality_name, emotion))

            row = cursor.fetchone()
            if not row:
                return None

            return {
                'image_data': row['image_data'],
                'content_type': row['content_type'],
                'width': row['width'],
                'height': row['height'],
                'file_size': row['file_size']
            }

    def has_avatar_image(self, personality_name: str, emotion: str) -> bool:
        """Check if an avatar image exists for the given personality and emotion."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT 1 FROM avatar_images
                WHERE personality_name = ? AND emotion = ?
            """, (personality_name, emotion))
            return cursor.fetchone() is not None

    def get_available_avatar_emotions(self, personality_name: str) -> List[str]:
        """Get list of emotions that have avatar images for a personality."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT emotion FROM avatar_images
                WHERE personality_name = ?
                ORDER BY emotion
            """, (personality_name,))
            return [row[0] for row in cursor.fetchall()]

    def has_all_avatar_emotions(self, personality_name: str) -> bool:
        """Check if a personality has all 6 emotion avatars."""
        emotions = self.get_available_avatar_emotions(personality_name)
        required = {'confident', 'happy', 'thinking', 'nervous', 'angry', 'shocked'}
        return required.issubset(set(emotions))

    def delete_avatar_images(self, personality_name: str) -> int:
        """Delete all avatar images for a personality.

        Returns:
            Number of images deleted
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                DELETE FROM avatar_images WHERE personality_name = ?
            """, (personality_name,))
            return cursor.rowcount

    def list_personalities_with_avatars(self) -> List[Dict[str, Any]]:
        """Get list of all personalities that have at least one avatar image.

        Returns:
            List of dicts with personality_name and emotion_count
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT personality_name, COUNT(*) as emotion_count
                FROM avatar_images
                GROUP BY personality_name
                ORDER BY personality_name
            """)
            return [
                {'personality_name': row['personality_name'], 'emotion_count': row['emotion_count']}
                for row in cursor.fetchall()
            ]

    def get_avatar_stats(self) -> Dict[str, Any]:
        """Get statistics about avatar images in the database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Total count
            cursor = conn.execute("SELECT COUNT(*) as count FROM avatar_images")
            total_count = cursor.fetchone()['count']

            # Total size
            cursor = conn.execute("SELECT SUM(file_size) as total_size FROM avatar_images")
            total_size = cursor.fetchone()['total_size'] or 0

            # Unique personalities
            cursor = conn.execute("SELECT COUNT(DISTINCT personality_name) as count FROM avatar_images")
            personality_count = cursor.fetchone()['count']

            # Personalities with all 6 emotions
            cursor = conn.execute("""
                SELECT COUNT(*) as count FROM (
                    SELECT personality_name FROM avatar_images
                    GROUP BY personality_name
                    HAVING COUNT(DISTINCT emotion) = 6
                )
            """)
            complete_count = cursor.fetchone()['count']

            return {
                'total_images': total_count,
                'total_size_bytes': total_size,
                'total_size_mb': round(total_size / (1024 * 1024), 2),
                'personality_count': personality_count,
                'complete_personality_count': complete_count
            }

    # Personality Seeding Methods
    def seed_personalities_from_json(self, json_path: str, overwrite: bool = False) -> Dict[str, int]:
        """Seed database with personalities from JSON file.

        Args:
            json_path: Path to personalities.json file
            overwrite: If True, overwrite existing personalities

        Returns:
            Dict with counts: {'added': N, 'skipped': M, 'updated': P}
        """
        import json as json_module
        from pathlib import Path

        json_file = Path(json_path)
        if not json_file.exists():
            logger.warning(f"Personalities JSON file not found: {json_path}")
            return {'added': 0, 'skipped': 0, 'updated': 0, 'error': 'File not found'}

        try:
            with open(json_file, 'r') as f:
                data = json_module.load(f)
        except Exception as e:
            logger.error(f"Error reading personalities JSON: {e}")
            return {'added': 0, 'skipped': 0, 'updated': 0, 'error': str(e)}

        personalities = data.get('personalities', {})
        added = 0
        skipped = 0
        updated = 0

        for name, config in personalities.items():
            existing = self.load_personality(name)

            if existing and not overwrite:
                skipped += 1
                continue

            if existing:
                updated += 1
            else:
                added += 1

            self.save_personality(name, config, source='personalities.json')

        logger.info(f"Seeded personalities from JSON: {added} added, {updated} updated, {skipped} skipped")
        return {'added': added, 'skipped': skipped, 'updated': updated}

    # Prompt Capture Methods (for AI decision debugging)
    def save_prompt_capture(self, capture: Dict[str, Any]) -> int:
        """Save a prompt capture for debugging AI decisions.

        Args:
            capture: Dict containing capture data with keys:
                - game_id, player_name, hand_number, phase
                - system_prompt, user_message, ai_response
                - pot_total, cost_to_call, pot_odds, player_stack
                - community_cards, player_hand, valid_actions
                - action_taken, raise_amount
                - model, latency_ms, input_tokens, output_tokens

        Returns:
            The ID of the inserted capture.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO prompt_captures (
                    -- Identity
                    game_id, player_name, hand_number,
                    -- Game State
                    phase, pot_total, cost_to_call, pot_odds, player_stack,
                    community_cards, player_hand, valid_actions,
                    -- Decision
                    action_taken, raise_amount,
                    -- Prompts (INPUT)
                    system_prompt, conversation_history, user_message, raw_request,
                    -- Response (OUTPUT)
                    ai_response, raw_api_response,
                    -- LLM Config
                    model, reasoning_effort,
                    -- Metrics
                    latency_ms, input_tokens, output_tokens,
                    -- Tracking
                    original_request_id,
                    -- User Annotations
                    tags, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                # Identity
                capture.get('game_id'),
                capture.get('player_name'),
                capture.get('hand_number'),
                # Game State
                capture.get('phase'),
                capture.get('pot_total'),
                capture.get('cost_to_call'),
                capture.get('pot_odds'),
                capture.get('player_stack'),
                json.dumps(capture.get('community_cards')) if capture.get('community_cards') else None,
                json.dumps(capture.get('player_hand')) if capture.get('player_hand') else None,
                json.dumps(capture.get('valid_actions')) if capture.get('valid_actions') else None,
                # Decision
                capture.get('action_taken'),
                capture.get('raise_amount'),
                # Prompts (INPUT)
                capture.get('system_prompt'),
                json.dumps(capture.get('conversation_history')) if capture.get('conversation_history') else None,
                capture.get('user_message'),
                capture.get('raw_request'),
                # Response (OUTPUT)
                capture.get('ai_response'),
                capture.get('raw_api_response'),
                # LLM Config
                capture.get('model'),
                capture.get('reasoning_effort'),
                # Metrics
                capture.get('latency_ms'),
                capture.get('input_tokens'),
                capture.get('output_tokens'),
                # Tracking
                capture.get('original_request_id'),
                # User Annotations
                json.dumps(capture.get('tags', [])),
                capture.get('notes'),
            ))
            conn.commit()
            return cursor.lastrowid

    def get_prompt_capture(self, capture_id: int) -> Optional[Dict[str, Any]]:
        """Get a single prompt capture by ID.

        Joins with api_usage to get cached_tokens, reasoning_tokens, and estimated_cost.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Join with api_usage to get usage metrics (cached tokens, reasoning tokens, cost)
            cursor = conn.execute("""
                SELECT pc.*,
                       au.cached_tokens,
                       au.reasoning_tokens,
                       au.estimated_cost
                FROM prompt_captures pc
                LEFT JOIN api_usage au ON pc.original_request_id = au.request_id
                WHERE pc.id = ?
            """, (capture_id,))
            row = cursor.fetchone()
            if not row:
                return None

            capture = dict(row)
            # Parse JSON fields
            for field in ['community_cards', 'player_hand', 'valid_actions', 'tags', 'conversation_history']:
                if capture.get(field):
                    try:
                        capture[field] = json.loads(capture[field])
                    except json.JSONDecodeError:
                        pass
            return capture

    def list_prompt_captures(
        self,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        action: Optional[str] = None,
        phase: Optional[str] = None,
        min_pot_odds: Optional[float] = None,
        max_pot_odds: Optional[float] = None,
        tags: Optional[List[str]] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List prompt captures with optional filtering.

        Returns:
            Dict with 'captures' list and 'total' count.
        """
        conditions = []
        params = []

        if game_id:
            conditions.append("game_id = ?")
            params.append(game_id)
        if player_name:
            conditions.append("player_name = ?")
            params.append(player_name)
        if action:
            conditions.append("action_taken = ?")
            params.append(action)
        if phase:
            conditions.append("phase = ?")
            params.append(phase)
        if min_pot_odds is not None:
            conditions.append("pot_odds >= ?")
            params.append(min_pot_odds)
        if max_pot_odds is not None:
            conditions.append("pot_odds <= ?")
            params.append(max_pot_odds)
        if tags:
            # Match any of the provided tags
            tag_conditions = []
            for tag in tags:
                tag_conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')
            conditions.append(f"({' OR '.join(tag_conditions)})")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Get total count
            count_cursor = conn.execute(
                f"SELECT COUNT(*) FROM prompt_captures {where_clause}",
                params
            )
            total = count_cursor.fetchone()[0]

            # Get captures with pagination
            query = f"""
                SELECT id, created_at, game_id, player_name, hand_number, phase,
                       action_taken, pot_total, cost_to_call, pot_odds, player_stack,
                       community_cards, player_hand, model, latency_ms, tags, notes
                FROM prompt_captures
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            cursor = conn.execute(query, params)

            captures = []
            for row in cursor.fetchall():
                capture = dict(row)
                # Parse JSON fields
                for field in ['community_cards', 'player_hand', 'tags']:
                    if capture.get(field):
                        try:
                            capture[field] = json.loads(capture[field])
                        except json.JSONDecodeError:
                            pass
                captures.append(capture)

            return {
                'captures': captures,
                'total': total
            }

    def get_prompt_capture_stats(self, game_id: Optional[str] = None) -> Dict[str, Any]:
        """Get aggregate statistics for prompt captures."""
        where_clause = "WHERE game_id = ?" if game_id else ""
        params = [game_id] if game_id else []

        with sqlite3.connect(self.db_path) as conn:
            # Count by action
            cursor = conn.execute(f"""
                SELECT action_taken, COUNT(*) as count
                FROM prompt_captures {where_clause}
                GROUP BY action_taken
            """, params)
            by_action = {row[0]: row[1] for row in cursor.fetchall()}

            # Count by phase
            cursor = conn.execute(f"""
                SELECT phase, COUNT(*) as count
                FROM prompt_captures {where_clause}
                GROUP BY phase
            """, params)
            by_phase = {row[0]: row[1] for row in cursor.fetchall()}

            # Suspicious folds (high pot odds)
            suspicious_params = params + [5.0]  # pot odds > 5:1
            cursor = conn.execute(f"""
                SELECT COUNT(*) FROM prompt_captures
                {where_clause}
                {'AND' if where_clause else 'WHERE'} action_taken = 'fold' AND pot_odds > ?
            """, suspicious_params)
            suspicious_folds = cursor.fetchone()[0]

            # Total captures
            cursor = conn.execute(f"SELECT COUNT(*) FROM prompt_captures {where_clause}", params)
            total = cursor.fetchone()[0]

            return {
                'total': total,
                'by_action': by_action,
                'by_phase': by_phase,
                'suspicious_folds': suspicious_folds
            }

    def update_prompt_capture_tags(
        self,
        capture_id: int,
        tags: List[str],
        notes: Optional[str] = None
    ) -> bool:
        """Update tags and notes for a prompt capture."""
        with sqlite3.connect(self.db_path) as conn:
            if notes is not None:
                conn.execute(
                    "UPDATE prompt_captures SET tags = ?, notes = ? WHERE id = ?",
                    (json.dumps(tags), notes, capture_id)
                )
            else:
                conn.execute(
                    "UPDATE prompt_captures SET tags = ? WHERE id = ?",
                    (json.dumps(tags), capture_id)
                )
            conn.commit()
            return conn.total_changes > 0

    def delete_prompt_captures(self, game_id: Optional[str] = None, before_date: Optional[str] = None) -> int:
        """Delete prompt captures, optionally filtered by game or date.

        Args:
            game_id: Delete captures for a specific game
            before_date: Delete captures before this date (ISO format)

        Returns:
            Number of captures deleted.
        """
        conditions = []
        params = []

        if game_id:
            conditions.append("game_id = ?")
            params.append(game_id)
        if before_date:
            conditions.append("created_at < ?")
            params.append(before_date)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(f"DELETE FROM prompt_captures {where_clause}", params)
            conn.commit()
            return cursor.rowcount