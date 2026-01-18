# Repository Code Migration Guide

This document provides the complete code migration path from `GamePersistence` to the new repository architecture. The data migration is covered in `REPOSITORY_MIGRATION_GUIDE.md`.

## Why Full Migration is Required

The new repository architecture uses a **different schema** than `GamePersistence`. They cannot run in parallel because:

1. `GamePersistence._init_db()` creates indexes on columns that don't exist in the new schema
2. Column names differ (e.g., `winner_name` vs `winners`, `observer_name` vs `observer`)
3. The new schema is intentionally cleaner and normalized differently

**You must migrate all code before deploying.**

---

## Files Requiring Code Changes

### Production Files (14 files)

| File | Persistence Calls | Priority |
|------|-------------------|----------|
| `flask_app/extensions.py` | Initializes persistence | CRITICAL |
| `flask_app/handlers/game_handler.py` | ~15 calls | HIGH |
| `flask_app/handlers/message_handler.py` | ~3 calls | HIGH |
| `flask_app/routes/game_routes.py` | ~10 calls | HIGH |
| `flask_app/routes/personality_routes.py` | ~8 calls | HIGH |
| `flask_app/routes/image_routes.py` | ~6 calls | HIGH |
| `flask_app/routes/stats_routes.py` | ~5 calls | MEDIUM |
| `flask_app/routes/prompt_debug_routes.py` | ~12 calls | MEDIUM |
| `flask_app/routes/admin_dashboard_routes.py` | ~4 calls | MEDIUM |
| `flask_app/routes/experiment_routes.py` | ~20 calls | LOW |
| `poker/personality_generator.py` | Constructor dependency | HIGH |
| `poker/character_images.py` | Constructor dependency | HIGH |
| `poker/auth.py` | Constructor dependency | HIGH |
| `core/llm/tracking.py` | ~3 calls | MEDIUM |

### Supporting Files (tests, scripts)

| File | Notes |
|------|-------|
| `tests/test_persistence.py` | Update or replace |
| `tests/test_experiment_routes.py` | Update mocks |
| `tests/test_tournament_flow.py` | Update mocks |
| `poker/memory/memory_manager.py` | Uses persistence for session context |
| `poker/memory/session_memory.py` | Uses persistence |
| `experiments/run_ai_tournament.py` | Update for experiments |

---

## Method Mapping: GamePersistence → Repositories

### GameRepository (game_repository.py)

| GamePersistence Method | Repository Method | Notes |
|------------------------|-------------------|-------|
| `save_game(game_id, state_machine, ...)` | `game.save(GameEntity)` | Create GameEntity first |
| `load_game(game_id)` | `game.find_by_id(game_id)` | Returns GameEntity |
| `delete_game(game_id)` | `game.delete(game_id)` | Same |
| `list_games(owner_id, limit)` | `game.find_recent(owner_id, limit)` | Returns List[GameEntity] |
| `load_llm_configs(game_id)` | `game.load_llm_configs(game_id)` | Same |
| `save_tournament_tracker(...)` | `tournament.save_tracker(TournamentTrackerEntity)` | Moved to tournament repo |
| `load_tournament_tracker(...)` | `tournament.load_tracker(game_id)` | Moved to tournament repo |

### MessageRepository (game_repository.py)

| GamePersistence Method | Repository Method | Notes |
|------------------------|-------------------|-------|
| `save_message(game_id, type, text)` | `messages.save(MessageEntity)` | Create entity first |
| `load_messages(game_id, limit)` | `messages.find_by_game_id(game_id, limit)` | Returns List[MessageEntity] |

### AIMemoryRepository (ai_memory_repository.py)

| GamePersistence Method | Repository Method | Notes |
|------------------------|-------------------|-------|
| `save_ai_player_state(...)` | `ai_memory.save_player_state(AIPlayerStateEntity)` | Create entity |
| `load_ai_player_states(game_id)` | `ai_memory.load_player_states(game_id)` | Returns Dict[str, Entity] |
| `save_personality_snapshot(...)` | `ai_memory.save_personality_snapshot(Entity)` | Create entity |
| `save_opponent_models(...)` | `ai_memory.save_opponent_model(OpponentModelEntity)` | One per opponent |
| `load_opponent_models(game_id)` | `ai_memory.load_opponent_models(game_id)` | Returns List[Entity] |
| `save_hand_commentary(...)` | `ai_memory.save_hand_commentary(HandCommentaryEntity)` | Create entity |
| `get_recent_reflections(...)` | `ai_memory.get_recent_reflections(game_id, player, limit)` | Same |

### PersonalityRepository (personality_repository.py)

| GamePersistence Method | Repository Method | Notes |
|------------------------|-------------------|-------|
| `save_personality(name, config, source)` | `personality.save(PersonalityEntity)` | Create entity |
| `load_personality(name)` | `personality.find_by_name(name)` | Returns PersonalityEntity |
| `list_personalities(limit)` | `personality.find_all(limit)` | Returns List[Entity] |
| `delete_personality(name)` | `personality.delete(name)` | Same |
| `save_avatar_image(...)` | `personality.save_avatar(AvatarImageEntity)` | Create entity |
| `load_avatar_image(name, emotion)` | `personality.load_avatar(name, emotion)` | Returns AvatarImageEntity |
| `load_avatar_image_with_metadata(...)` | `personality.load_avatar(name, emotion)` | Entity has all metadata |
| `load_full_avatar_image(...)` | `personality.load_avatar(...)` | Access `.full_image_data` |
| `get_available_avatar_emotions(name)` | `personality.get_available_emotions(name)` | Same |
| `delete_avatar_images(name)` | `personality.delete_avatars(name)` | Same |

### EmotionalStateRepository (emotional_state_repository.py)

| GamePersistence Method | Repository Method | Notes |
|------------------------|-------------------|-------|
| `save_emotional_state(...)` | `emotional_state.save_emotional_state(Entity)` | Create entity |
| `load_emotional_state(game_id, player)` | `emotional_state.load_emotional_state(...)` | Returns Entity |
| `load_all_emotional_states(game_id)` | `emotional_state.load_all_emotional_states(...)` | Returns Dict |
| `save_controller_state(...)` | `emotional_state.save_controller_state(Entity)` | Create entity |
| `load_controller_state(...)` | `emotional_state.load_controller_state(...)` | Returns Entity |
| `load_all_controller_states(game_id)` | `emotional_state.load_all_controller_states(...)` | Returns Dict |
| `delete_emotional_state_for_game(...)` | `emotional_state.delete_by_game_id(...)` | Combined |
| `delete_controller_state_for_game(...)` | `emotional_state.delete_by_game_id(...)` | Combined |

### HandHistoryRepository (hand_history_repository.py)

| GamePersistence Method | Repository Method | Notes |
|------------------------|-------------------|-------|
| `save_hand_history(recorded_hand)` | `hand_history.save(HandHistoryEntity)` | Create entity |
| `load_hand_history(game_id, limit)` | `hand_history.find_by_game_id(game_id, limit)` | Returns List[Entity] |
| `get_hand_count(game_id)` | `hand_history.get_hand_count(game_id)` | Same |
| `get_session_stats(game_id, player)` | `hand_history.get_session_stats(game_id, player)` | Same |
| `delete_hand_history_for_game(...)` | `hand_history.delete_by_game_id(...)` | Same |

### TournamentRepository (tournament_repository.py)

| GamePersistence Method | Repository Method | Notes |
|------------------------|-------------------|-------|
| `save_tournament_result(game_id, result)` | `tournament.save_result(TournamentResultEntity)` | Create entity |
| `get_tournament_result(game_id)` | `tournament.get_result(game_id)` | Returns Entity |
| `get_career_stats(player_name)` | `tournament.get_career_stats(player_name)` | Returns Entity |
| `get_tournament_history(player, limit)` | `tournament.get_tournament_history(player, limit)` | Returns List |
| `save_tournament_tracker(...)` | `tournament.save_tracker(TournamentTrackerEntity)` | Create entity |
| `load_tournament_tracker(...)` | `tournament.load_tracker(game_id)` | Returns Entity |

### LLMTrackingRepository (llm_tracking_repository.py)

| GamePersistence Method | Repository Method | Notes |
|------------------------|-------------------|-------|
| `record_api_usage(...)` | `llm_tracking.save_usage(ApiUsageEntity)` | Create entity |
| `get_enabled_models()` | `llm_tracking.get_enabled_models_by_provider()` | Different return type |
| `get_all_enabled_models()` | `llm_tracking.get_enabled_models()` | Returns List[Entity] |

### DebugRepository (debug_repository.py)

| GamePersistence Method | Repository Method | Notes |
|------------------------|-------------------|-------|
| `save_prompt_capture(capture)` | `debug.save_prompt_capture(PromptCaptureEntity)` | Create entity |
| `get_prompt_capture(capture_id)` | `debug.get_prompt_capture(capture_id)` | Returns Entity |
| `list_prompt_captures(...)` | `debug.list_prompt_captures(...)` | Returns List[Entity] |
| `get_prompt_capture_stats(...)` | `debug.get_prompt_capture_stats(...)` | Same |
| `delete_prompt_captures(...)` | `debug.delete_prompt_captures(...)` | Same |
| `save_decision_analysis(...)` | `debug.save_decision_analysis(Entity)` | Create entity |
| `get_decision_analysis(id)` | `debug.get_decision_analysis(id)` | Returns Entity |
| `list_decision_analyses(...)` | `debug.list_decision_analyses(...)` | Returns List[Entity] |

### ConfigRepository (config_repository.py)

| GamePersistence Method | Repository Method | Notes |
|------------------------|-------------------|-------|
| `get_setting(key, default)` | `config.get_setting(key, default)` | Same |
| `set_setting(key, value)` | `config.set_setting(key, value)` | Same |
| `get_all_settings()` | `config.get_all_settings()` | Same |
| `delete_setting(key)` | `config.delete_setting(key)` | Same |
| `get_user_by_id(user_id)` | `config.get_user(user_id)` | Returns UserEntity |
| `get_user_by_email(email)` | `config.get_user_by_email(email)` | Returns UserEntity |
| `save_or_update_user(...)` | `config.save_user(UserEntity)` | Create entity |
| `get_user_by_linked_guest(guest_id)` | `config.get_user_by_linked_guest(guest_id)` | Returns Entity |
| `link_guest_to_user(...)` | `config.link_guest_to_user(user_id, guest_id)` | Same |

---

## Step-by-Step Migration

### Step 1: Update extensions.py

Remove `GamePersistence` initialization. Use only `RepositoryFactory`.

```python
# flask_app/extensions.py

# REMOVE these:
# from poker.persistence import GamePersistence
# persistence = None

# KEEP/UPDATE:
from poker.repositories.factory import RepositoryFactory

repository_factory = None

def init_persistence() -> RepositoryFactory:
    """Initialize repository factory."""
    global repository_factory

    db_path = config.DB_PATH
    repository_factory = RepositoryFactory(db_path, initialize_schema=False)

    return repository_factory

def get_repository_factory() -> RepositoryFactory:
    """Get the repository factory."""
    global repository_factory
    if repository_factory is None:
        init_persistence()
    return repository_factory
```

### Step 2: Update PersonalityGenerator

```python
# poker/personality_generator.py

from poker.repositories.factory import RepositoryFactory
from poker.repositories.protocols import PersonalityEntity
from datetime import datetime

class PersonalityGenerator:
    def __init__(self, repository_factory: RepositoryFactory = None):
        self._repo = repository_factory
        self._llm = LLMClient()

    @property
    def repo(self) -> RepositoryFactory:
        if self._repo is None:
            from flask_app.extensions import get_repository_factory
            self._repo = get_repository_factory()
        return self._repo

    def save_personality(self, name: str, config: dict, source: str = 'ai_generated'):
        entity = PersonalityEntity(
            name=name,
            config=config,
            source=source,
            created_at=datetime.now(),
            last_used=None
        )
        self.repo.personality.save(entity)

    def load_personality(self, name: str) -> Optional[dict]:
        entity = self.repo.personality.find_by_name(name)
        if entity:
            return entity.config
        return None
```

### Step 3: Update CharacterImageService

```python
# poker/character_images.py

from poker.repositories.factory import RepositoryFactory
from poker.repositories.protocols import AvatarImageEntity
from datetime import datetime

class CharacterImageService:
    def __init__(self, personality_generator=None, repository_factory: RepositoryFactory = None):
        self.personality_generator = personality_generator
        self._repo = repository_factory

    @property
    def repo(self) -> RepositoryFactory:
        if self._repo is None:
            from flask_app.extensions import get_repository_factory
            self._repo = get_repository_factory()
        return self._repo

    def save_avatar(self, personality_name: str, emotion: str, image_data: bytes, ...):
        entity = AvatarImageEntity(
            personality_name=personality_name,
            emotion=emotion,
            image_data=image_data,
            thumbnail_data=thumbnail_data,
            full_image_data=full_image_data,
            generation_prompt=prompt,
            created_at=datetime.now()
        )
        self.repo.personality.save_avatar(entity)

    def load_avatar(self, personality_name: str, emotion: str) -> Optional[bytes]:
        entity = self.repo.personality.load_avatar(personality_name, emotion)
        if entity:
            return entity.thumbnail_data or entity.image_data
        return None
```

### Step 4: Update AuthManager

```python
# poker/auth.py

from poker.repositories.factory import RepositoryFactory
from poker.repositories.protocols import UserEntity
from datetime import datetime

class AuthManager:
    def __init__(self, app=None, repository_factory: RepositoryFactory = None, oauth=None):
        self.app = app
        self._repo = repository_factory
        self.oauth = oauth

    @property
    def repo(self) -> RepositoryFactory:
        if self._repo is None:
            from flask_app.extensions import get_repository_factory
            self._repo = get_repository_factory()
        return self._repo

    def get_user(self, user_id: str) -> Optional[dict]:
        entity = self.repo.config.get_user(user_id)
        if entity:
            return {
                'id': entity.id,
                'email': entity.email,
                'name': entity.name,
                'picture': entity.picture
            }
        return None

    def save_user(self, user_data: dict):
        entity = UserEntity(
            id=user_data['id'],
            email=user_data['email'],
            name=user_data['name'],
            picture=user_data.get('picture'),
            created_at=datetime.now(),
            last_login=datetime.now(),
            linked_guest_id=user_data.get('linked_guest_id')
        )
        self.repo.config.save_user(entity)
```

### Step 5: Update Route Files

Example for `game_routes.py`:

```python
# flask_app/routes/game_routes.py

from flask_app.extensions import get_repository_factory
from poker.repositories.protocols import GameEntity, MessageEntity
from datetime import datetime

@game_bp.route('/api/games', methods=['GET'])
def list_games():
    repo = get_repository_factory()
    owner_id = get_current_user_id()

    # OLD: games = persistence.list_games(owner_id=owner_id)
    # NEW:
    games = repo.game.find_recent(owner_id=owner_id)

    return jsonify([{
        'game_id': g.id,
        'phase': g.phase,
        'num_players': g.num_players,
        'pot_size': g.pot_size,
        'created_at': g.created_at.isoformat(),
        'updated_at': g.updated_at.isoformat()
    } for g in games])

@game_bp.route('/api/games/<game_id>', methods=['DELETE'])
def delete_game(game_id):
    repo = get_repository_factory()

    # OLD: persistence.delete_game(game_id)
    # NEW:
    repo.game.delete(game_id)

    return jsonify({'status': 'deleted'})
```

### Step 6: Update LLM Tracking

```python
# core/llm/tracking.py

from poker.repositories.protocols import ApiUsageEntity
from datetime import datetime

def record_usage(game_id, player_name, call_type, model, ...):
    from flask_app.extensions import get_repository_factory
    repo = get_repository_factory()

    entity = ApiUsageEntity(
        game_id=game_id,
        owner_id=owner_id,
        player_name=player_name,
        hand_number=hand_number,
        call_type=call_type,
        model=model,
        provider=provider,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
        latency_ms=latency_ms,
        timestamp=datetime.now(),
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=total_cost
    )
    repo.llm_tracking.save_usage(entity)
```

---

## Testing the Migration

### 1. Unit Tests

```bash
# Create test for new repositories
docker compose exec backend python -m pytest tests/repositories/ -v
```

### 2. Integration Test

```python
# Test full game lifecycle
docker compose exec backend python -c "
from flask_app.extensions import get_repository_factory
from poker.repositories.protocols import PersonalityEntity
from datetime import datetime

repo = get_repository_factory()

# Test personality
p = PersonalityEntity(
    name='TestMigration',
    config={'style': 'test'},
    source='test',
    created_at=datetime.now()
)
repo.personality.save(p)
loaded = repo.personality.find_by_name('TestMigration')
assert loaded is not None
print('Personality test: PASSED')

# Test config
repo.config.set_setting('migration_test', 'success')
val = repo.config.get_setting('migration_test')
assert val == 'success'
print('Config test: PASSED')

print('All integration tests PASSED')
"
```

### 3. Manual Testing

1. Start the application
2. Create a new game
3. Play a few hands
4. Check that personalities load
5. Check that avatars display
6. Verify API usage is tracked

---

## Rollback Procedure

If migration fails:

1. **Restore old code**: `git checkout HEAD~1 -- poker/ flask_app/`
2. **Restore database**: `mv poker_games_backup.db poker_games.db`
3. **Restart**: `docker compose restart backend`

---

## Checklist

- [ ] Update `flask_app/extensions.py` - remove GamePersistence
- [ ] Update `poker/personality_generator.py` - use RepositoryFactory
- [ ] Update `poker/character_images.py` - use RepositoryFactory
- [ ] Update `poker/auth.py` - use RepositoryFactory
- [ ] Update `flask_app/handlers/game_handler.py`
- [ ] Update `flask_app/handlers/message_handler.py`
- [ ] Update `flask_app/routes/game_routes.py`
- [ ] Update `flask_app/routes/personality_routes.py`
- [ ] Update `flask_app/routes/image_routes.py`
- [ ] Update `flask_app/routes/stats_routes.py`
- [ ] Update `flask_app/routes/prompt_debug_routes.py`
- [ ] Update `flask_app/routes/admin_dashboard_routes.py`
- [ ] Update `flask_app/routes/experiment_routes.py`
- [ ] Update `core/llm/tracking.py`
- [ ] Update `poker/memory/memory_manager.py`
- [ ] Update `poker/memory/session_memory.py`
- [ ] Run data migration script
- [ ] Run integration tests
- [ ] Manual testing
- [ ] Delete `poker/persistence.py` (after all tests pass)
