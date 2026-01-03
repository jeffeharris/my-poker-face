"""Debug and diagnostic routes."""

import logging
from flask import Blueprint, jsonify, request

from ..services import game_state_service
from ..services.elasticity_service import format_elasticity_data
from .. import config

logger = logging.getLogger(__name__)

debug_bp = Blueprint('debug', __name__)


@debug_bp.route('/debug')
def debug_page():
    """Debug page with links to debug endpoints - only available in development."""
    if not config.is_development:
        return jsonify({'error': 'Debug page only available in development mode'}), 403

    active_games = game_state_service.list_game_ids()

    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Poker Debug Console</title>
        <style>
            body { font-family: monospace; background: #1a1a2e; color: #eee; padding: 20px; }
            h1 { color: #00d4ff; }
            h2 { color: #ff6b6b; margin-top: 30px; }
            .section { background: #16213e; padding: 15px; border-radius: 8px; margin: 10px 0; }
            a { color: #4ecca3; }
            .endpoint { margin: 10px 0; padding: 10px; background: #0f3460; border-radius: 4px; }
            .method { color: #ff9f1c; font-weight: bold; }
            .url { color: #4ecca3; }
            .desc { color: #aaa; font-size: 0.9em; }
            input, select { background: #0f3460; color: #eee; border: 1px solid #4ecca3; padding: 8px; border-radius: 4px; margin: 5px; }
            button { background: #4ecca3; color: #1a1a2e; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-weight: bold; }
            button:hover { background: #3db892; }
            pre { background: #0f3460; padding: 15px; border-radius: 4px; overflow-x: auto; }
            .games-list { margin: 10px 0; }
            .game-id { background: #0f3460; padding: 5px 10px; border-radius: 4px; margin: 5px; display: inline-block; }
        </style>
    </head>
    <body>
        <h1>Poker Debug Console</h1>
        <p>Development mode is <strong style="color: #4ecca3;">ENABLED</strong></p>

        <div class="section">
            <h2>Active Games</h2>
            <div class="games-list">
    '''

    if active_games:
        for game_id in active_games:
            html += f'<span class="game-id">{game_id}</span> '
    else:
        html += '<em>No active games</em>'

    html += '''
            </div>
            <p><a href="/games">View saved games</a></p>
        </div>

        <div class="section">
            <h2>Tilt System Debug</h2>
            <p class="desc">Test the tilt modifier system that affects AI decision-making</p>

            <div class="endpoint">
                <span class="method">GET</span>
                <span class="url">/api/game/{game_id}/tilt-debug</span>
                <p class="desc">View tilt state for all AI players</p>
                <input type="text" id="tilt-game-id" placeholder="game_id" style="width: 300px;">
                <button onclick="fetchTilt()">Fetch Tilt States</button>
            </div>

            <div class="endpoint">
                <span class="method">POST</span>
                <span class="url">/api/game/{game_id}/tilt-debug/{player_name}</span>
                <p class="desc">Set tilt state for testing</p>
                <input type="text" id="set-tilt-game-id" placeholder="game_id" style="width: 200px;">
                <input type="text" id="set-tilt-player" placeholder="player_name" style="width: 150px;">
                <br>
                <select id="tilt-level">
                    <option value="0">None (0.0)</option>
                    <option value="0.3">Mild (0.3)</option>
                    <option value="0.5">Moderate (0.5)</option>
                    <option value="0.8" selected>Severe (0.8)</option>
                    <option value="1.0">Maximum (1.0)</option>
                </select>
                <select id="tilt-source">
                    <option value="bad_beat">Bad Beat</option>
                    <option value="bluff_called">Bluff Called</option>
                    <option value="big_loss">Big Loss</option>
                    <option value="losing_streak">Losing Streak</option>
                </select>
                <input type="text" id="tilt-nemesis" placeholder="nemesis (optional)" style="width: 150px;">
                <button onclick="setTilt()">Set Tilt</button>
            </div>
            <pre id="tilt-result">Results will appear here...</pre>
        </div>

        <div class="section">
            <h2>Memory System Debug</h2>
            <div class="endpoint">
                <span class="method">GET</span>
                <span class="url">/api/game/{game_id}/memory-debug</span>
                <p class="desc">View AI memory state (session memory, opponent models)</p>
                <input type="text" id="memory-game-id" placeholder="game_id" style="width: 300px;">
                <button onclick="fetchMemory()">Fetch Memory</button>
            </div>
            <pre id="memory-result">Results will appear here...</pre>
        </div>

        <div class="section">
            <h2>Elasticity System Debug</h2>
            <div class="endpoint">
                <span class="method">GET</span>
                <span class="url">/api/game/{game_id}/elasticity</span>
                <p class="desc">View elastic personality traits for all AI players</p>
                <input type="text" id="elasticity-game-id" placeholder="game_id" style="width: 300px;">
                <button onclick="fetchElasticity()">Fetch Elasticity</button>
            </div>
            <pre id="elasticity-result">Results will appear here...</pre>
        </div>

        <div class="section">
            <h2>Pressure Stats</h2>
            <div class="endpoint">
                <span class="method">GET</span>
                <span class="url">/api/game/{game_id}/pressure-stats</span>
                <p class="desc">View pressure events and statistics</p>
                <input type="text" id="pressure-game-id" placeholder="game_id" style="width: 300px;">
                <button onclick="fetchPressure()">Fetch Pressure Stats</button>
            </div>
            <pre id="pressure-result">Results will appear here...</pre>
        </div>

        <div class="section">
            <h2>Game State</h2>
            <div class="endpoint">
                <span class="method">GET</span>
                <span class="url">/api/game/{game_id}/diagnostic</span>
                <p class="desc">Full game diagnostic info</p>
                <input type="text" id="diag-game-id" placeholder="game_id" style="width: 300px;">
                <button onclick="fetchDiagnostic()">Fetch Diagnostic</button>
            </div>
            <pre id="diag-result">Results will appear here...</pre>
        </div>

        <script>
            async function fetchJson(url, options = {}) {
                try {
                    const resp = await fetch(url, options);
                    return await resp.json();
                } catch (e) {
                    return {error: e.message};
                }
            }

            async function fetchTilt() {
                const gameId = document.getElementById('tilt-game-id').value;
                const result = await fetchJson(`/api/game/${gameId}/tilt-debug`);
                document.getElementById('tilt-result').textContent = JSON.stringify(result, null, 2);
            }

            async function setTilt() {
                const gameId = document.getElementById('set-tilt-game-id').value;
                const player = encodeURIComponent(document.getElementById('set-tilt-player').value);
                const data = {
                    tilt_level: parseFloat(document.getElementById('tilt-level').value),
                    tilt_source: document.getElementById('tilt-source').value,
                    nemesis: document.getElementById('tilt-nemesis').value || null
                };
                const result = await fetchJson(`/api/game/${gameId}/tilt-debug/${player}`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                });
                document.getElementById('tilt-result').textContent = JSON.stringify(result, null, 2);
            }

            async function fetchMemory() {
                const gameId = document.getElementById('memory-game-id').value;
                const result = await fetchJson(`/api/game/${gameId}/memory-debug`);
                document.getElementById('memory-result').textContent = JSON.stringify(result, null, 2);
            }

            async function fetchElasticity() {
                const gameId = document.getElementById('elasticity-game-id').value;
                const result = await fetchJson(`/api/game/${gameId}/elasticity`);
                document.getElementById('elasticity-result').textContent = JSON.stringify(result, null, 2);
            }

            async function fetchPressure() {
                const gameId = document.getElementById('pressure-game-id').value;
                const result = await fetchJson(`/api/game/${gameId}/pressure-stats`);
                document.getElementById('pressure-result').textContent = JSON.stringify(result, null, 2);
            }

            async function fetchDiagnostic() {
                const gameId = document.getElementById('diag-game-id').value;
                const result = await fetchJson(`/api/game/${gameId}/diagnostic`);
                document.getElementById('diag-result').textContent = JSON.stringify(result, null, 2);
            }
        </script>
    </body>
    </html>
    '''
    return html


@debug_bp.route('/api/game/<game_id>/diagnostic', methods=['GET'])
def api_game_diagnostic(game_id):
    """Get diagnostic information about a game's state."""
    current_game_data = game_state_service.get_game(game_id)

    lock = game_state_service.game_locks.get(game_id)
    lock_held = lock.locked() if lock else False

    diagnostic = {
        'game_id': game_id,
        'in_memory': current_game_data is not None,
        'lock_exists': lock is not None,
        'lock_held': lock_held,
    }

    if current_game_data:
        state_machine = current_game_data['state_machine']
        game_state = state_machine.game_state
        current_player = game_state.current_player

        diagnostic.update({
            'phase': str(state_machine.current_phase).split('.')[-1],
            'awaiting_action': game_state.awaiting_action,
            'current_player': current_player.name,
            'current_player_is_human': current_player.is_human,
            'current_player_is_folded': current_player.is_folded,
            'game_started_flag': current_game_data.get('game_started', False),
            'has_ai_controllers': 'ai_controllers' in current_game_data,
            'player_count': len(game_state.players),
            'pot': game_state.pot,
        })

        is_stuck = (
            game_state.awaiting_action and
            not current_player.is_human and
            not current_player.is_folded
        )
        diagnostic['appears_stuck'] = is_stuck
        if is_stuck:
            diagnostic['stuck_reason'] = 'AI turn pending but no progress'

    return jsonify(diagnostic)


@debug_bp.route('/api/game/<game_id>/elasticity', methods=['GET'])
def get_elasticity_data(game_id):
    """Get current elasticity data for all AI players."""
    game_data = game_state_service.get_game(game_id)
    if not game_data or 'elasticity_manager' not in game_data:
        return jsonify({'error': 'Game not found or elasticity not enabled'}), 404

    elasticity_manager = game_data['elasticity_manager']
    elasticity_data = format_elasticity_data(elasticity_manager)

    return jsonify(elasticity_data)


@debug_bp.route('/api/game/<game_id>/memory-debug', methods=['GET'])
def get_memory_debug(game_id):
    """Get current memory state for debugging."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    if 'memory_manager' not in game_data:
        return jsonify({'error': 'Memory manager not initialized', 'working': False}), 200

    memory_manager = game_data['memory_manager']

    debug_info = {
        'working': True,
        'game_id': memory_manager.game_id,
        'hand_count': memory_manager.hand_count,
        'initialized_players': list(memory_manager.initialized_players),
        'session_memories': {},
        'opponent_models': {},
        'current_hand': None,
        'completed_hands_count': len(memory_manager.hand_recorder.completed_hands)
    }

    for player_name, session in memory_manager.session_memories.items():
        debug_info['session_memories'][player_name] = {
            'hands_played': session.context.hands_played,
            'hands_won': session.context.hands_won,
            'current_streak': session.context.current_streak,
            'streak_count': session.context.streak_count,
            'total_winnings': session.context.total_winnings,
            'hand_memories_count': len(session.hand_memories),
            'context_preview': session.get_context_for_prompt(100)[:200] if session.hand_memories else 'No hands yet'
        }

    all_models = memory_manager.opponent_model_manager.models
    for observer, targets in all_models.items():
        debug_info['opponent_models'][observer] = {}
        for target, model in targets.items():
            debug_info['opponent_models'][observer][target] = {
                'hands_observed': model.tendencies.hands_observed,
                'vpip': round(model.tendencies.vpip, 2),
                'pfr': round(model.tendencies.pfr, 2),
                'aggression_factor': round(model.tendencies.aggression_factor, 2),
                'play_style': model.tendencies.get_play_style_label(),
                'summary': model.get_prompt_summary()
            }

    if memory_manager.hand_recorder.current_hand:
        current = memory_manager.hand_recorder.current_hand
        debug_info['current_hand'] = {
            'hand_number': current.hand_number,
            'actions_recorded': len(current.actions),
            'phase': current.actions[-1].phase if current.actions else 'PRE_FLOP'
        }

    return jsonify(debug_info)


@debug_bp.route('/api/game/<game_id>/tilt-debug', methods=['GET'])
def get_tilt_debug(game_id):
    """Get tilt state for all AI players."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    ai_controllers = game_data.get('ai_controllers', {})
    if not ai_controllers:
        return jsonify({'error': 'No AI controllers found'}), 200

    tilt_info = {}
    for player_name, controller in ai_controllers.items():
        if hasattr(controller, 'tilt_state'):
            state = controller.tilt_state
            tilt_info[player_name] = {
                'tilt_level': round(state.tilt_level, 2),
                'tilt_category': state.get_tilt_category(),
                'tilt_source': state.tilt_source or 'none',
                'nemesis': state.nemesis,
                'losing_streak': state.losing_streak,
                'recent_losses': state.recent_losses[-3:] if state.recent_losses else []
            }
        else:
            tilt_info[player_name] = {'error': 'No tilt state (old controller?)'}

    return jsonify({
        'game_id': game_id,
        'tilt_states': tilt_info
    })


@debug_bp.route('/api/game/<game_id>/tilt-debug/<player_name>', methods=['POST'])
def set_tilt_debug(game_id, player_name):
    """Set tilt state for an AI player - for testing."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    ai_controllers = game_data.get('ai_controllers', {})
    if player_name not in ai_controllers:
        return jsonify({
            'error': f'AI player "{player_name}" not found',
            'available_players': list(ai_controllers.keys())
        }), 404

    controller = ai_controllers[player_name]
    if not hasattr(controller, 'tilt_state'):
        return jsonify({'error': 'Controller has no tilt state'}), 500

    data = request.get_json() or {}
    state = controller.tilt_state

    if 'tilt_level' in data:
        state.tilt_level = max(0.0, min(1.0, float(data['tilt_level'])))
    if 'tilt_source' in data:
        state.tilt_source = data['tilt_source']
    if 'nemesis' in data:
        state.nemesis = data['nemesis']
    if 'losing_streak' in data:
        state.losing_streak = int(data['losing_streak'])

    return jsonify({
        'success': True,
        'player': player_name,
        'new_state': {
            'tilt_level': round(state.tilt_level, 2),
            'tilt_category': state.get_tilt_category(),
            'tilt_source': state.tilt_source,
            'nemesis': state.nemesis,
            'losing_streak': state.losing_streak
        }
    })


@debug_bp.route('/api/game/<game_id>/pressure-stats', methods=['GET'])
def get_pressure_stats(game_id):
    """Get pressure event statistics for the game."""
    game_data = game_state_service.get_game(game_id)
    if not game_data or 'pressure_stats' not in game_data:
        return jsonify({'error': 'Game not found or stats not available'}), 404

    pressure_stats = game_data['pressure_stats']
    return jsonify(pressure_stats.get_session_summary())
