"""Admin dashboard routes for LLM usage analysis, model management, and debug tools."""

import logging
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from flask import Blueprint, jsonify, request

from .. import config
from ..services import game_state_service
from core.llm import UsageTracker

logger = logging.getLogger(__name__)

admin_dashboard_bp = Blueprint('admin_dashboard', __name__, url_prefix='/admin')


def _get_db_path() -> str:
    """Get the database path based on environment."""
    if Path('/app/data').exists():
        return '/app/data/poker_games.db'
    return str(Path(__file__).parent.parent.parent / 'poker_games.db')


def _get_date_modifier(range_param: str) -> str:
    """Convert range parameter to SQLite datetime modifier for parameterized queries.

    Returns a modifier string to be used with datetime('now', ?).
    This approach prevents SQL injection by using parameterized queries.
    """
    modifiers = {
        '24h': '-1 day',
        '7d': '-7 days',
        '30d': '-30 days',
        'all': '-100 years',  # Effectively all time
    }
    return modifiers.get(range_param, '-7 days')


def _check_admin_auth() -> tuple[bool, str]:
    """Check if the request has valid admin authentication.

    Authentication is required when ADMIN_TOKEN is set in environment.
    Token can be provided via:
    - Authorization: Bearer <token> header
    - ?admin_token=<token> query parameter (for browser access)

    Returns:
        Tuple of (is_authenticated, error_message)
    """
    admin_token = os.environ.get('ADMIN_TOKEN')

    # If no token is configured, allow access (but log warning in production-like envs)
    if not admin_token:
        if not config.is_development:
            logger.warning("ADMIN_TOKEN not set - admin endpoints unprotected")
        return True, ""

    # Check Authorization header first
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        provided_token = auth_header[7:]
        if secrets.compare_digest(provided_token, admin_token):
            return True, ""

    # Check query parameter (for browser access to HTML pages)
    provided_token = request.args.get('admin_token', '')
    if provided_token and secrets.compare_digest(provided_token, admin_token):
        return True, ""

    return False, "Invalid or missing admin token"


def _admin_required(f):
    """Decorator to restrict admin endpoints with authentication.

    Security:
    - In production: ADMIN_TOKEN is REQUIRED for access
    - In development: Access allowed by default, optionally require token with ADMIN_REQUIRE_TOKEN=true
    - Token can be provided via Authorization header or query parameter
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        admin_token = os.environ.get('ADMIN_TOKEN')

        # In production: ADMIN_TOKEN is required
        if not config.is_development:
            if not admin_token:
                return jsonify({
                    'error': 'Admin dashboard requires ADMIN_TOKEN to be configured in production'
                }), 403

            is_authenticated, error_msg = _check_admin_auth()
            if not is_authenticated:
                return jsonify({
                    'error': error_msg,
                    'hint': 'Set Authorization: Bearer YOUR_TOKEN header'
                }), 401

            return f(*args, **kwargs)

        # In development: optionally require token
        require_token = os.environ.get('ADMIN_REQUIRE_TOKEN', 'false').lower() == 'true'
        if admin_token and require_token:
            is_authenticated, error_msg = _check_admin_auth()
            if not is_authenticated:
                return jsonify({
                    'error': error_msg,
                    'hint': 'Add ?admin_token=YOUR_TOKEN to the URL or set Authorization: Bearer YOUR_TOKEN header'
                }), 401

        return f(*args, **kwargs)
    return decorated


# Keep old decorator name as alias for backwards compatibility
_dev_only = _admin_required


# =============================================================================
# Dashboard Root - Redirect to React Admin
# =============================================================================

@admin_dashboard_bp.route('/')
@_dev_only
def dashboard():
    """Redirect to React admin dashboard."""
    return jsonify({
        'message': 'Admin dashboard has moved to React UI',
        'redirect': '/?view=admin'
    })


# =============================================================================
# API Endpoints (for AJAX updates)
# =============================================================================

@admin_dashboard_bp.route('/api/summary')
@_dev_only
def api_summary():
    """JSON endpoint for dashboard summary data."""
    range_param = request.args.get('range', '7d')
    date_modifier = _get_date_modifier(range_param)

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            cursor = conn.execute("""
                SELECT
                    COUNT(*) as total_calls,
                    COALESCE(SUM(estimated_cost), 0) as total_cost,
                    COALESCE(AVG(latency_ms), 0) as avg_latency,
                    COALESCE(SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0), 0) as error_rate
                FROM api_usage
                WHERE created_at >= datetime('now', ?)
            """, (date_modifier,))
            summary = dict(cursor.fetchone())

            return jsonify({'success': True, 'summary': summary})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# Cost Analysis - Redirect to React Admin
# =============================================================================

@admin_dashboard_bp.route('/costs')
@_dev_only
def costs():
    """Redirect to React admin dashboard."""
    return jsonify({
        'message': 'Cost analysis has moved to React UI',
        'redirect': '/?view=admin'
    })


# =============================================================================
# Performance Metrics - Redirect to React Admin
# =============================================================================

@admin_dashboard_bp.route('/performance')
@_dev_only
def performance():
    """Redirect to React admin dashboard."""
    return jsonify({
        'message': 'Performance metrics has moved to React UI',
        'redirect': '/?view=admin'
    })


# =============================================================================
# Prompt Viewer - Redirect to React Admin
# =============================================================================

@admin_dashboard_bp.route('/prompts')
@_dev_only
def prompts():
    """Redirect to React admin dashboard."""
    return jsonify({
        'message': 'Prompt viewer has moved to React UI',
        'redirect': '/?view=admin'
    })


# =============================================================================
# Models Manager - Redirect to React Admin
# =============================================================================

@admin_dashboard_bp.route('/models')
@_dev_only
def models():
    """Redirect to React admin dashboard."""
    return jsonify({
        'message': 'Model manager has moved to React UI',
        'redirect': '/?view=admin'
    })


def _render_models_migration_needed():
    """Render message when migration hasn't been run."""
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Models - Admin Dashboard</title>
        <style>
            body { font-family: sans-serif; background: #1a1a2e; color: #eee; margin: 0; }
            .sidebar { width: 200px; background: #16213e; position: fixed; height: 100%; padding: 20px; }
            .sidebar h2 { color: #00d4ff; margin: 0 0 30px 0; font-size: 1.2em; }
            .sidebar nav a { display: block; color: #aaa; text-decoration: none; padding: 10px 15px; margin: 5px 0; border-radius: 6px; }
            .sidebar nav a:hover { background: #0f3460; color: #eee; }
            .sidebar nav a.active { background: #4ecca3; color: #1a1a2e; }
            .content { margin-left: 220px; padding: 30px; }
            h1 { color: #00d4ff; }
            .notice { background: #0f3460; padding: 20px; border-radius: 8px; border-left: 4px solid #f59e0b; }
            code { background: #16213e; padding: 2px 6px; border-radius: 4px; }
        </style>
    </head>
    <body>
        <div class="sidebar">
            <h2>Admin Dashboard</h2>
            <nav>
                <a href="/admin/">Dashboard</a>
                <a href="/admin/costs">Cost Analysis</a>
                <a href="/admin/performance">Performance</a>
                <a href="/admin/prompts">Prompts</a>
                <a href="/admin/models" class="active">Models</a>
                <a href="/admin/pricing">Pricing</a>
                <a href="/admin/debug">Debug Tools</a>
            </nav>
        </div>
        <div class="content">
            <h1>Model Manager</h1>
            <div class="notice">
                <p><strong>Migration Required</strong></p>
                <p>The <code>enabled_models</code> table doesn't exist. Restart the backend to run migrations.</p>
                <p><code>docker compose restart backend</code></p>
            </div>
        </div>
    </body>
    </html>
    '''


def _render_models(rows):
    """Render models manager page HTML."""

    # Group by provider
    by_provider = {}
    for row in rows:
        provider = row['provider']
        if provider not in by_provider:
            by_provider[provider] = []
        by_provider[provider].append(row)

    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Models - Admin Dashboard</title>
        <style>
            * { box-sizing: border-box; }
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #eee; margin: 0; }
            .sidebar { width: 200px; background: #16213e; position: fixed; height: 100%; padding: 20px; }
            .sidebar h2 { color: #00d4ff; margin: 0 0 30px 0; font-size: 1.2em; }
            .sidebar nav a { display: block; color: #aaa; text-decoration: none; padding: 10px 15px; margin: 5px 0; border-radius: 6px; }
            .sidebar nav a:hover { background: #0f3460; color: #eee; }
            .sidebar nav a.active { background: #4ecca3; color: #1a1a2e; }
            .content { margin-left: 220px; padding: 30px; }
            h1 { color: #00d4ff; margin: 0 0 10px 0; }
            .subtitle { color: #888; margin-bottom: 30px; }
            .card { background: #16213e; padding: 20px; border-radius: 8px; margin-bottom: 20px; }
            .card h3 { margin: 0 0 15px 0; color: #eee; display: flex; align-items: center; gap: 10px; }
            .provider-badge { font-size: 0.7em; padding: 4px 8px; background: #0f3460; border-radius: 4px; }
            .model-row { display: flex; align-items: center; padding: 12px; border-bottom: 1px solid #0f3460; gap: 15px; }
            .model-row:last-child { border-bottom: none; }
            .model-name { flex: 1; font-weight: 500; }
            .model-caps { display: flex; gap: 8px; }
            .cap-badge { font-size: 0.75em; padding: 2px 6px; border-radius: 4px; background: #0f3460; color: #888; }
            .cap-badge.active { background: #4ecca3; color: #1a1a2e; }
            .toggle { position: relative; width: 50px; height: 26px; }
            .toggle input { opacity: 0; width: 0; height: 0; }
            .toggle .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background: #0f3460; border-radius: 13px; transition: 0.3s; }
            .toggle .slider:before { position: absolute; content: ""; height: 20px; width: 20px; left: 3px; bottom: 3px; background: #888; border-radius: 50%; transition: 0.3s; }
            .toggle input:checked + .slider { background: #4ecca3; }
            .toggle input:checked + .slider:before { transform: translateX(24px); background: white; }
            .disabled { opacity: 0.5; }
            #status { position: fixed; bottom: 20px; right: 20px; padding: 12px 20px; border-radius: 6px; display: none; z-index: 100; }
            #status.success { display: block; background: #10b981; }
            #status.error { display: block; background: #ef4444; }
        </style>
    </head>
    <body>
        <div class="sidebar">
            <h2>Admin Dashboard</h2>
            <nav>
                <a href="/admin/">Dashboard</a>
                <a href="/admin/costs">Cost Analysis</a>
                <a href="/admin/performance">Performance</a>
                <a href="/admin/prompts">Prompts</a>
                <a href="/admin/models" class="active">Models</a>
                <a href="/admin/pricing">Pricing</a>
                <a href="/admin/debug">Debug Tools</a>
            </nav>
        </div>
        <div class="content">
            <h1>Model Manager</h1>
            <p class="subtitle">Enable or disable models available in game setup</p>
    '''

    provider_colors = {
        'openai': '#10b981', 'anthropic': '#f59e0b', 'groq': '#3b82f6',
        'deepseek': '#8b5cf6', 'mistral': '#ef4444', 'google': '#06b6d4', 'xai': '#ec4899',
    }

    for provider, models in sorted(by_provider.items()):
        color = provider_colors.get(provider, '#6b7280')
        enabled_count = sum(1 for m in models if m['enabled'])

        html += f'''
            <div class="card">
                <h3>
                    <span class="provider-badge" style="background: {color}; color: white;">{provider.upper()}</span>
                    {enabled_count}/{len(models)} enabled
                </h3>
        '''

        for model in models:
            checked = 'checked' if model['enabled'] else ''
            disabled_class = '' if model['enabled'] else 'disabled'

            # Capability badges
            caps = []
            if model['supports_reasoning']:
                caps.append('<span class="cap-badge active">reasoning</span>')
            if model['supports_json_mode']:
                caps.append('<span class="cap-badge active">json</span>')
            if model['supports_image_gen']:
                caps.append('<span class="cap-badge active">images</span>')

            html += f'''
                <div class="model-row {disabled_class}" id="row-{model['id']}">
                    <label class="toggle">
                        <input type="checkbox" {checked} onchange="toggleModel({model['id']}, this.checked)">
                        <span class="slider"></span>
                    </label>
                    <span class="model-name">{model['model']}</span>
                    <div class="model-caps">{''.join(caps)}</div>
                </div>
            '''

        html += '</div>'

    html += '''
        </div>
        <div id="status"></div>

        <script>
            async function toggleModel(id, enabled) {
                const row = document.getElementById('row-' + id);
                try {
                    const resp = await fetch('/admin/api/models/' + id + '/toggle', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({enabled: enabled})
                    });
                    const result = await resp.json();
                    if (result.success) {
                        row.classList.toggle('disabled', !enabled);
                        showStatus('success', enabled ? 'Model enabled' : 'Model disabled');
                    } else {
                        showStatus('error', result.error || 'Failed to update');
                        // Revert toggle
                        row.querySelector('input').checked = !enabled;
                    }
                } catch (e) {
                    showStatus('error', e.message);
                    row.querySelector('input').checked = !enabled;
                }
            }

            function showStatus(type, message) {
                const el = document.getElementById('status');
                el.className = type;
                el.textContent = message;
                setTimeout(() => el.className = '', 2000);
            }
        </script>
    </body>
    </html>
    '''
    return html


@admin_dashboard_bp.route('/api/models/<int:model_id>/toggle', methods=['POST'])
@_dev_only
def api_toggle_model(model_id):
    """Toggle a model's enabled or user_enabled status.

    Request body:
        field: 'enabled' or 'user_enabled' (default: 'enabled')
        enabled: boolean - the new value

    Cascade logic:
        - If field=user_enabled and enabled=true: also set enabled=1 (System must be ON for User to be ON)
        - If field=enabled and enabled=false: also set user_enabled=0 (User must be OFF if System is OFF)
    """
    data = request.get_json()
    field = data.get('field', 'enabled')
    enabled = data.get('enabled', False)

    # Validate field parameter
    if field not in ('enabled', 'user_enabled'):
        return jsonify({'success': False, 'error': 'Invalid field. Must be "enabled" or "user_enabled"'}), 400

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            # Get current state for cascade logic
            current = conn.execute(
                "SELECT enabled, user_enabled FROM enabled_models WHERE id = ?",
                (model_id,)
            ).fetchone()

            if not current:
                return jsonify({'success': False, 'error': 'Model not found'}), 404

            new_enabled = current['enabled']
            new_user_enabled = current['user_enabled']

            if field == 'enabled':
                new_enabled = 1 if enabled else 0
                # Cascade: if turning system OFF, also turn user OFF
                if not enabled:
                    new_user_enabled = 0
            else:  # field == 'user_enabled'
                new_user_enabled = 1 if enabled else 0
                # Cascade: if turning user ON, also turn system ON
                if enabled:
                    new_enabled = 1

            conn.execute("""
                UPDATE enabled_models
                SET enabled = ?, user_enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_enabled, new_user_enabled, model_id))

            return jsonify({
                'success': True,
                'enabled': bool(new_enabled),
                'user_enabled': bool(new_user_enabled)
            })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/models', methods=['GET'])
@_dev_only
def api_list_models():
    """List all models with their enabled status."""
    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            # Check if table exists (migration may not have run)
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='enabled_models'
            """)
            if not cursor.fetchone():
                return jsonify({
                    'success': False,
                    'error': 'Migration required: enabled_models table does not exist'
                }), 503

            cursor = conn.execute("""
                SELECT id, provider, model, enabled, user_enabled, display_name, notes,
                       supports_reasoning, supports_json_mode, supports_image_gen,
                       supports_img2img, sort_order, updated_at
                FROM enabled_models
                ORDER BY provider, sort_order
            """)
            models = [dict(row) for row in cursor.fetchall()]
            return jsonify({'success': True, 'models': models})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# Pricing Manager - Redirect to React Admin
# Note: The JSON API is at /admin/pricing with GET method (see list_pricing below)
# =============================================================================

# The route /admin/pricing with GET method returns JSON (see list_pricing function below)


def _render_pricing(rows):
    """Render pricing page HTML."""

    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Pricing - Admin Dashboard</title>
        <style>
            * { box-sizing: border-box; }
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #eee; margin: 0; }
            .sidebar { width: 200px; background: #16213e; position: fixed; height: 100%; padding: 20px; }
            .sidebar h2 { color: #00d4ff; margin: 0 0 30px 0; font-size: 1.2em; }
            .sidebar nav a { display: block; color: #aaa; text-decoration: none; padding: 10px 15px; margin: 5px 0; border-radius: 6px; }
            .sidebar nav a:hover { background: #0f3460; color: #eee; }
            .sidebar nav a.active { background: #4ecca3; color: #1a1a2e; }
            .content { margin-left: 220px; padding: 30px; }
            h1 { color: #00d4ff; margin: 0 0 10px 0; }
            .subtitle { color: #888; margin-bottom: 30px; }
            .card { background: #16213e; padding: 20px; border-radius: 8px; margin-bottom: 20px; }
            .card h3 { margin: 0 0 15px 0; color: #eee; }
            table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
            th, td { padding: 10px; text-align: left; border-bottom: 1px solid #0f3460; }
            th { color: #888; font-weight: normal; }
            .cost { color: #4ecca3; }
            .form-row { display: flex; gap: 10px; margin-bottom: 15px; flex-wrap: wrap; }
            .form-row input, .form-row select { background: #0f3460; color: #eee; border: 1px solid #4ecca3; padding: 8px 12px; border-radius: 4px; }
            .form-row button { background: #4ecca3; color: #1a1a2e; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-weight: bold; }
            .form-row button:hover { background: #3db892; }
            .delete-btn { background: #ef4444; color: white; border: none; padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 0.8em; }
            .delete-btn:hover { background: #dc2626; }
            #status { margin-top: 10px; padding: 10px; border-radius: 4px; display: none; }
            #status.success { display: block; background: #10b981; }
            #status.error { display: block; background: #ef4444; }
        </style>
    </head>
    <body>
        <div class="sidebar">
            <h2>Admin Dashboard</h2>
            <nav>
                <a href="/admin/">Dashboard</a>
                <a href="/admin/costs">Cost Analysis</a>
                <a href="/admin/performance">Performance</a>
                <a href="/admin/prompts">Prompts</a>
                <a href="/admin/models">Models</a>
                <a href="/admin/pricing" class="active">Pricing</a>
                <a href="/admin/debug">Debug Tools</a>
            </nav>
        </div>
        <div class="content">
            <h1>Pricing Manager</h1>
            <p class="subtitle">Manage model pricing for cost calculations</p>

            <div class="card">
                <h3>Add New Pricing</h3>
                <div class="form-row">
                    <input type="text" id="new-provider" placeholder="Provider (e.g., openai)">
                    <input type="text" id="new-model" placeholder="Model (e.g., gpt-4o)">
                    <input type="text" id="new-unit" placeholder="Unit (e.g., input_tokens_1m)">
                    <input type="number" step="0.0001" id="new-cost" placeholder="Cost ($)">
                    <button onclick="addPricing()">Add</button>
                </div>
                <div id="status"></div>
            </div>

            <div class="card">
                <h3>Current Pricing</h3>
                <table>
                    <thead>
                        <tr>
                            <th>Provider</th>
                            <th>Model</th>
                            <th>Unit</th>
                            <th>Cost</th>
                            <th>Valid From</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody id="pricing-table">
    '''

    for row in rows:
        html += f'''
                        <tr id="row-{row['id']}">
                            <td>{row['provider']}</td>
                            <td>{row['model']}</td>
                            <td>{row['unit']}</td>
                            <td class="cost">${row['cost']}</td>
                            <td>{row['valid_from'] or '-'}</td>
                            <td><button class="delete-btn" onclick="deletePricing({row['id']})">Delete</button></td>
                        </tr>
        '''

    html += '''
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            async function addPricing() {
                const data = {
                    provider: document.getElementById('new-provider').value,
                    model: document.getElementById('new-model').value,
                    unit: document.getElementById('new-unit').value,
                    cost: parseFloat(document.getElementById('new-cost').value)
                };
                try {
                    const resp = await fetch('/admin/pricing', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(data)
                    });
                    const result = await resp.json();
                    showStatus(result.success ? 'success' : 'error', result.message || result.error);
                    if (result.success) setTimeout(() => location.reload(), 1000);
                } catch (e) {
                    showStatus('error', e.message);
                }
            }

            async function deletePricing(id) {
                if (!confirm('Delete this pricing entry?')) return;
                try {
                    const resp = await fetch('/admin/pricing/' + id, {method: 'DELETE'});
                    const result = await resp.json();
                    showStatus(result.success ? 'success' : 'error', result.message || result.error);
                    if (result.success) document.getElementById('row-' + id).remove();
                } catch (e) {
                    showStatus('error', e.message);
                }
            }

            function showStatus(type, message) {
                const el = document.getElementById('status');
                el.className = type;
                el.textContent = message;
            }
        </script>
    </body>
    </html>
    '''
    return html


# =============================================================================
# Prompt Playground API
# =============================================================================

@admin_dashboard_bp.route('/api/playground/captures')
@_dev_only
def api_playground_captures():
    """List captured prompts for the playground.

    Query params:
        call_type: Filter by call type (e.g., 'commentary', 'personality_generation')
        provider: Filter by LLM provider
        limit: Max results (default 50)
        offset: Pagination offset (default 0)
        date_from: Filter by start date (ISO format)
        date_to: Filter by end date (ISO format)
    """
    from ..extensions import persistence

    try:
        result = persistence.list_playground_captures(
            call_type=request.args.get('call_type'),
            provider=request.args.get('provider'),
            limit=int(request.args.get('limit', 50)),
            offset=int(request.args.get('offset', 0)),
            date_from=request.args.get('date_from'),
            date_to=request.args.get('date_to'),
        )

        stats = persistence.get_playground_capture_stats()

        return jsonify({
            'success': True,
            'captures': result['captures'],
            'total': result['total'],
            'stats': stats,
        })

    except Exception as e:
        logger.error(f"Playground captures error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/playground/captures/<int:capture_id>')
@_dev_only
def api_playground_capture(capture_id):
    """Get a single playground capture by ID."""
    from ..extensions import persistence

    try:
        capture = persistence.get_prompt_capture(capture_id)

        if not capture:
            return jsonify({'success': False, 'error': 'Capture not found'}), 404

        return jsonify({
            'success': True,
            'capture': capture,
        })

    except Exception as e:
        logger.error(f"Playground capture error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/playground/captures/<int:capture_id>/replay', methods=['POST'])
@_dev_only
def api_playground_replay(capture_id):
    """Replay a captured prompt with optional modifications.

    Request body:
        system_prompt: Modified system prompt (optional)
        user_message: Modified user message (optional)
        conversation_history: Modified history (optional)
        use_history: Whether to include history (default: True)
        provider: LLM provider to use (optional)
        model: Model to use (optional)
        reasoning_effort: Reasoning effort (optional)
    """
    from ..extensions import persistence
    from core.llm import LLMClient, CallType

    try:
        capture = persistence.get_prompt_capture(capture_id)
        if not capture:
            return jsonify({'success': False, 'error': 'Capture not found'}), 404

        data = request.get_json() or {}

        # Use modified prompts or originals
        system_prompt = data.get('system_prompt', capture.get('system_prompt', ''))
        user_message = data.get('user_message', capture.get('user_message', ''))
        provider = data.get('provider', capture.get('provider', 'openai')).lower()
        model = data.get('model', capture.get('model'))
        reasoning_effort = data.get('reasoning_effort', capture.get('reasoning_effort', 'minimal'))

        # Handle conversation history
        use_history = data.get('use_history', True)
        conversation_history = data.get('conversation_history', capture.get('conversation_history', []))

        # Create LLM client
        client = LLMClient(provider=provider, model=model, reasoning_effort=reasoning_effort)

        # Build messages array
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if use_history and conversation_history:
            for msg in conversation_history:
                messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", "")
                })

        messages.append({"role": "user", "content": user_message})

        # Check if JSON format requested
        combined_text = (system_prompt or '') + (user_message or '')
        use_json_format = 'json' in combined_text.lower()

        response = client.complete(
            messages=messages,
            json_format=use_json_format,
            call_type=CallType.DEBUG_REPLAY,
        )

        return jsonify({
            'success': True,
            'original_response': capture.get('ai_response', ''),
            'new_response': response.content,
            'provider_used': response.provider,
            'model_used': response.model,
            'reasoning_effort_used': reasoning_effort,
            'input_tokens': response.input_tokens,
            'output_tokens': response.output_tokens,
            'latency_ms': response.latency_ms,
            'messages_count': len(messages),
            'used_history': use_history and bool(conversation_history),
        })

    except Exception as e:
        logger.error(f"Playground replay error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/playground/stats')
@_dev_only
def api_playground_stats():
    """Get aggregate statistics for playground captures."""
    from ..extensions import persistence

    try:
        stats = persistence.get_playground_capture_stats()
        return jsonify({'success': True, 'stats': stats})

    except Exception as e:
        logger.error(f"Playground stats error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/playground/cleanup', methods=['POST'])
@_dev_only
def api_playground_cleanup():
    """Delete old playground captures.

    Request body:
        retention_days: Delete captures older than this many days (default: from config)
    """
    from ..extensions import persistence
    from core.llm.capture_config import get_retention_days

    try:
        data = request.get_json() or {}
        retention_days = data.get('retention_days', get_retention_days())

        if retention_days <= 0:
            return jsonify({
                'success': True,
                'message': 'Unlimited retention configured, no cleanup performed',
                'deleted': 0,
            })

        deleted = persistence.cleanup_old_captures(retention_days)

        return jsonify({
            'success': True,
            'message': f'Deleted {deleted} captures older than {retention_days} days',
            'deleted': deleted,
        })

    except Exception as e:
        logger.error(f"Playground cleanup error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# Image Playground API (capture viewing, replay, reference images, avatars)
# =============================================================================

@admin_dashboard_bp.route('/api/reference-images', methods=['POST'])
@_dev_only
def api_upload_reference_image():
    """Upload a reference image for image-to-image generation.

    Accepts: multipart/form-data with 'file' or JSON with 'url'
    Returns: { reference_id, width, height }
    """
    import uuid
    import requests as http_requests

    try:
        image_data = None
        content_type = 'image/png'
        source = 'upload'
        original_url = None
        width = None
        height = None

        # Check for file upload
        if 'file' in request.files:
            file = request.files['file']
            if file.filename:
                image_data = file.read()
                content_type = file.content_type or 'image/png'
                source = 'upload'
        else:
            # Check for URL in JSON body
            data = request.get_json() or {}
            url = data.get('url')
            if url:
                # Download the image from URL
                response = http_requests.get(url, timeout=30)
                response.raise_for_status()
                image_data = response.content
                content_type = response.headers.get('Content-Type', 'image/png')
                source = 'url'
                original_url = url

        if not image_data:
            return jsonify({'success': False, 'error': 'No image provided'}), 400

        # Try to get image dimensions
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(image_data))
            width, height = img.size
        except ImportError:
            # PIL not installed; skip dimension extraction but allow upload to proceed
            pass
        except Exception as e:
            logger.debug(f"Could not get image dimensions: {e}")

        # Generate unique ID
        reference_id = str(uuid.uuid4())

        # Store in database
        with sqlite3.connect(_get_db_path()) as conn:
            conn.execute("""
                INSERT INTO reference_images (id, image_data, width, height, content_type, source, original_url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (reference_id, image_data, width, height, content_type, source, original_url))

        return jsonify({
            'success': True,
            'reference_id': reference_id,
            'width': width,
            'height': height,
        })

    except Exception as e:
        logger.error(f"Reference image upload error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/reference-images/<reference_id>')
@_dev_only
def api_get_reference_image(reference_id: str):
    """Serve a reference image by ID.

    Returns the raw image data with appropriate content-type header.
    """
    from flask import Response

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT image_data, content_type FROM reference_images WHERE id = ?",
                (reference_id,)
            )
            row = cursor.fetchone()

            if not row:
                return jsonify({'success': False, 'error': 'Reference image not found'}), 404

            return Response(
                row['image_data'],
                mimetype=row['content_type'] or 'image/png',
                headers={'Cache-Control': 'max-age=31536000'}  # Cache for 1 year
            )

    except Exception as e:
        logger.error(f"Reference image fetch error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/playground/captures/<int:capture_id>/replay-image', methods=['POST'])
@_dev_only
def api_playground_replay_image(capture_id: int):
    """Replay an image capture with modifications.

    Request body:
        prompt: Modified prompt
        provider: Image provider to use
        model: Model to use
        size: Image size (e.g., "512x512")
        reference_image_id: Optional reference image

    Returns: {
        original_image_url,
        new_image_url,  # base64 data URL
        provider_used,
        model_used,
        latency_ms,
        estimated_cost
    }
    """
    from ..extensions import persistence
    from core.llm import LLMClient, CallType
    import base64

    try:
        capture = persistence.get_prompt_capture(capture_id)
        if not capture:
            return jsonify({'success': False, 'error': 'Capture not found'}), 404

        # Verify it's an image capture
        if not capture.get('is_image_capture'):
            return jsonify({'success': False, 'error': 'Not an image capture'}), 400

        data = request.get_json() or {}

        # Use modified values or originals
        prompt = data.get('prompt', capture.get('image_prompt', ''))
        provider = data.get('provider', capture.get('provider', 'pollinations')).lower()
        model = data.get('model', capture.get('model'))
        size = data.get('size', capture.get('image_size', '512x512'))
        reference_image_id = data.get('reference_image_id')

        # Check if model supports img2img when reference image is provided
        seed_image_url = None
        if reference_image_id:
            # Check model's img2img support
            with sqlite3.connect(_get_db_path()) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT supports_img2img FROM enabled_models WHERE provider = ? AND model = ?",
                    (provider, model)
                )
                model_row = cursor.fetchone()
                supports_img2img = model_row['supports_img2img'] if model_row else False

                if not supports_img2img:
                    return jsonify({
                        'success': False,
                        'error': f'Model "{model}" does not support image-to-image generation. Please select a model that supports img2img, or remove the reference image.',
                    }), 400

                # Fetch the reference image and convert to data URI
                cursor = conn.execute(
                    "SELECT image_data, content_type FROM reference_images WHERE id = ?",
                    (reference_image_id,)
                )
                ref_row = cursor.fetchone()
                if ref_row and ref_row['image_data']:
                    content_type = ref_row['content_type'] or 'image/png'
                    b64_data = base64.b64encode(ref_row['image_data']).decode('utf-8')
                    seed_image_url = f"data:{content_type};base64,{b64_data}"
                    logger.info(f"Using reference image for img2img: {reference_image_id} ({len(b64_data)} bytes base64)")
                else:
                    logger.warning(f"Reference image not found: {reference_image_id}")

        # Create LLM client for the provider
        client = LLMClient(provider=provider, model=model)

        # Generate the new image
        response = client.generate_image(
            prompt=prompt,
            size=size,
            call_type=CallType.DEBUG_REPLAY,
            seed_image_url=seed_image_url,
            reference_image_id=reference_image_id,
        )

        if response.is_error:
            return jsonify({
                'success': False,
                'error': response.error_message or 'Image generation failed',
            }), 500

        # Download the new image and convert to base64 data URL
        new_image_url = None
        if response.url:
            try:
                import requests as http_requests
                img_response = http_requests.get(response.url, timeout=30)
                img_response.raise_for_status()
                img_data = img_response.content
                content_type = img_response.headers.get('Content-Type', 'image/png')
                b64_data = base64.b64encode(img_data).decode('utf-8')
                new_image_url = f"data:{content_type};base64,{b64_data}"
            except Exception as e:
                logger.warning(f"Failed to download new image: {e}")
                new_image_url = response.url  # Fall back to URL

        # Get original image as base64 if available
        original_image_url = None
        if capture.get('image_data'):
            content_type = 'image/png'
            b64_data = base64.b64encode(capture['image_data']).decode('utf-8')
            original_image_url = f"data:{content_type};base64,{b64_data}"
        elif capture.get('image_url'):
            original_image_url = capture['image_url']

        return jsonify({
            'success': True,
            'original_image_url': original_image_url,
            'new_image_url': new_image_url,
            'provider_used': response.provider,
            'model_used': response.model,
            'latency_ms': int(response.latency_ms) if response.latency_ms else None,
            'size_used': size,
        })

    except Exception as e:
        logger.error(f"Image replay error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/playground/captures/<int:capture_id>/assign-avatar', methods=['POST'])
@_dev_only
def api_assign_avatar_from_capture(capture_id: int):
    """Assign a captured/replayed image as a personality avatar.

    Request body:
        personality_name: Target personality
        emotion: Target emotion
        use_replayed: True to use replayed image, False for original
        replayed_image_data: Base64 image data (if use_replayed)
    """
    from ..extensions import persistence
    import base64

    try:
        capture = persistence.get_prompt_capture(capture_id)
        if not capture:
            return jsonify({'success': False, 'error': 'Capture not found'}), 404

        data = request.get_json() or {}
        personality_name = data.get('personality_name')
        emotion = data.get('emotion', 'neutral')
        use_replayed = data.get('use_replayed', False)
        replayed_image_data = data.get('replayed_image_data')

        if not personality_name:
            return jsonify({'success': False, 'error': 'personality_name is required'}), 400

        # Get the image data
        if use_replayed and replayed_image_data:
            # Extract base64 data from data URL if needed
            if replayed_image_data.startswith('data:'):
                # Parse data URL: data:image/png;base64,xxxxx
                parts = replayed_image_data.split(',', 1)
                if len(parts) == 2:
                    image_data = base64.b64decode(parts[1])
                else:
                    return jsonify({'success': False, 'error': 'Invalid image data format'}), 400
            else:
                image_data = base64.b64decode(replayed_image_data)
        elif capture.get('image_data'):
            image_data = capture['image_data']
        else:
            return jsonify({'success': False, 'error': 'No image data available'}), 400

        # Save to avatar_images table
        with sqlite3.connect(_get_db_path()) as conn:
            # Check if avatar exists for this personality/emotion
            cursor = conn.execute(
                "SELECT id FROM avatar_images WHERE personality_name = ? AND emotion = ?",
                (personality_name, emotion)
            )
            existing = cursor.fetchone()

            if existing:
                # Update existing
                conn.execute("""
                    UPDATE avatar_images
                    SET image_data = ?, content_type = 'image/png', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (image_data, existing[0]))
            else:
                # Insert new
                conn.execute("""
                    INSERT INTO avatar_images (personality_name, emotion, image_data, content_type)
                    VALUES (?, ?, ?, 'image/png')
                """, (personality_name, emotion, image_data))

        return jsonify({
            'success': True,
            'message': f'Avatar assigned for {personality_name} ({emotion})',
            'personality_name': personality_name,
            'emotion': emotion,
        })

    except Exception as e:
        logger.error(f"Avatar assignment error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/image-providers')
@_dev_only
def api_get_image_providers():
    """Get list of enabled image providers with their models and size presets.

    Returns providers that support image generation (supports_image_gen=1).
    """
    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            # Get enabled image generation models
            cursor = conn.execute("""
                SELECT provider, model, display_name, supports_img2img
                FROM enabled_models
                WHERE enabled = 1 AND supports_image_gen = 1
                ORDER BY provider, sort_order
            """)

            # Group by provider
            providers = {}
            for row in cursor.fetchall():
                provider = row['provider']
                if provider not in providers:
                    providers[provider] = {
                        'id': provider,
                        'name': provider.title(),
                        'models': [],
                        'size_presets': _get_size_presets(provider),
                    }
                providers[provider]['models'].append({
                    'id': row['model'],
                    'name': row['display_name'] or row['model'],
                    'supports_img2img': bool(row['supports_img2img']),
                })

            return jsonify({
                'success': True,
                'providers': list(providers.values()),
            })

    except Exception as e:
        logger.error(f"Image providers error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def _get_size_presets(provider: str) -> list:
    """Get recommended size presets for a provider."""
    # Common presets that work across providers
    common_presets = [
        {'label': '1:1 Small (512x512)', 'value': '512x512', 'cost': '$'},
        {'label': '1:1 Medium (1024x1024)', 'value': '1024x1024', 'cost': '$$'},
    ]

    provider_presets = {
        'openai': [
            {'label': '1:1 (1024x1024)', 'value': '1024x1024', 'cost': '$$'},
            {'label': 'Portrait (1024x1792)', 'value': '1024x1792', 'cost': '$$$'},
            {'label': 'Landscape (1792x1024)', 'value': '1792x1024', 'cost': '$$$'},
        ],
        'pollinations': common_presets + [
            {'label': '16:9 (1024x576)', 'value': '1024x576', 'cost': '$$'},
            {'label': '9:16 (576x1024)', 'value': '576x1024', 'cost': '$$'},
        ],
        'runware': common_presets + [
            {'label': '16:9 (1024x576)', 'value': '1024x576', 'cost': '$$'},
            {'label': '9:16 (576x1024)', 'value': '576x1024', 'cost': '$$'},
        ],
        'xai': [
            {'label': '1:1 (1024x1024)', 'value': '1024x1024', 'cost': '$$'},
        ],
    }

    return provider_presets.get(provider, common_presets)


# =============================================================================
# Prompt Template Management
# =============================================================================

@admin_dashboard_bp.route('/api/prompts/templates')
@_dev_only
def api_list_templates():
    """List all prompt templates.

    Returns:
        JSON with list of template summaries (name, version, section_count, hash)
    """
    from poker.prompt_manager import PromptManager
    from poker.prompts import extract_variables

    try:
        manager = PromptManager()
        templates = []

        for name in sorted(manager.list_templates()):
            template = manager.get_template(name)
            # Extract variables from all sections
            all_content = '\n'.join(template.sections.values())
            variables = extract_variables(all_content)

            templates.append({
                'name': template.name,
                'version': template.version,
                'section_count': len(template.sections),
                'hash': template.template_hash,
                'variables': variables,
            })

        return jsonify({
            'success': True,
            'templates': templates,
            'total': len(templates),
        })

    except Exception as e:
        logger.error(f"Error listing templates: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/prompts/templates/<template_name>')
@_dev_only
def api_get_template(template_name: str):
    """Get a single template with full content.

    Args:
        template_name: Name of the template

    Returns:
        JSON with full template details including all sections
    """
    from poker.prompt_manager import PromptManager
    from poker.prompts import validate_template_name, extract_variables

    # Security: validate template name
    if not validate_template_name(template_name):
        return jsonify({'success': False, 'error': 'Invalid template name'}), 400

    try:
        manager = PromptManager()
        template = manager.get_template(template_name)

        # Extract variables from all sections
        all_content = '\n'.join(template.sections.values())
        variables = extract_variables(all_content)

        return jsonify({
            'success': True,
            'template': {
                'name': template.name,
                'version': template.version,
                'sections': template.sections,
                'hash': template.template_hash,
                'variables': variables,
            }
        })

    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 404
    except Exception as e:
        logger.error(f"Error getting template {template_name}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/prompts/templates/<template_name>', methods=['PUT'])
@_dev_only
def api_update_template(template_name: str):
    """Update a template by saving to its YAML file.

    Args:
        template_name: Name of the template

    Request body:
        {
            "sections": {"section_name": "content", ...},
            "version": "1.0.1" (optional)
        }

    Returns:
        JSON with success status and new hash
    """
    from poker.prompt_manager import PromptManager
    from poker.prompts import validate_template_name, validate_template_schema

    # Security: validate template name
    if not validate_template_name(template_name):
        return jsonify({'success': False, 'error': 'Invalid template name'}), 400

    try:
        data = request.get_json()
        if not data or 'sections' not in data:
            return jsonify({'success': False, 'error': 'Missing sections'}), 400

        sections = data['sections']
        version = data.get('version')

        # Validate sections is a dict of strings
        if not isinstance(sections, dict):
            return jsonify({'success': False, 'error': 'sections must be a dict'}), 400

        for key, value in sections.items():
            if not isinstance(key, str) or not isinstance(value, str):
                return jsonify({'success': False, 'error': 'Section keys and values must be strings'}), 400

        # Validate schema (required sections)
        is_valid, error = validate_template_schema(template_name, sections)
        if not is_valid:
            return jsonify({'success': False, 'error': error}), 400

        # Save the template
        manager = PromptManager()

        # Verify template exists
        try:
            manager.get_template(template_name)
        except ValueError:
            return jsonify({'success': False, 'error': f"Template '{template_name}' not found"}), 404

        # Save to YAML file
        success = manager.save_template(template_name, sections, version)

        if success:
            # Get the new hash
            updated = manager.get_template(template_name)
            return jsonify({
                'success': True,
                'message': 'Template updated',
                'new_hash': updated.template_hash,
                'new_version': updated.version,
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to save template'}), 500

    except Exception as e:
        logger.error(f"Error updating template {template_name}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/prompts/templates/<template_name>/preview', methods=['POST'])
@_dev_only
def api_preview_template(template_name: str):
    """Preview a template render with sample variables.

    Args:
        template_name: Name of the template

    Request body:
        {
            "sections": {"section_name": "content", ...} (optional, uses current if not provided),
            "variables": {"var_name": "value", ...}
        }

    Returns:
        JSON with rendered output and any missing variables
    """
    from poker.prompt_manager import PromptManager, PromptTemplate
    from poker.prompts import validate_template_name, extract_variables

    # Security: validate template name
    if not validate_template_name(template_name):
        return jsonify({'success': False, 'error': 'Invalid template name'}), 400

    try:
        data = request.get_json() or {}
        variables = data.get('variables', {})
        custom_sections = data.get('sections')

        manager = PromptManager()

        # Get the template (or use custom sections)
        if custom_sections:
            template = PromptTemplate(
                name=template_name,
                sections=custom_sections
            )
        else:
            template = manager.get_template(template_name)

        # Find all variables needed
        all_content = '\n'.join(template.sections.values())
        required_vars = set(extract_variables(all_content))
        provided_vars = set(variables.keys())
        missing_vars = required_vars - provided_vars

        # Render with provided variables (fill missing with placeholders)
        render_vars = {var: f'[{var}]' for var in required_vars}
        render_vars.update(variables)

        try:
            rendered = template.render(**render_vars)
            render_error = None
        except Exception as e:
            rendered = None
            render_error = str(e)

        return jsonify({
            'success': True,
            'rendered': rendered,
            'render_error': render_error,
            'required_variables': sorted(required_vars),
            'missing_variables': sorted(missing_vars),
        })

    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 404
    except Exception as e:
        logger.error(f"Error previewing template {template_name}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# Pricing Management API
# =============================================================================

@admin_dashboard_bp.route('/pricing', methods=['GET'])
def list_pricing():
    """List all pricing entries, optionally filtered.

    Query params:
        provider: Filter by provider (e.g., 'openai')
        model: Filter by model (e.g., 'gpt-4o')
        current_only: If 'true', only show currently valid prices
    """
    provider = request.args.get('provider')
    model = request.args.get('model')
    current_only = request.args.get('current_only', 'false').lower() == 'true'

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            query = "SELECT * FROM model_pricing WHERE 1=1"
            params = []

            if provider:
                query += " AND provider = ?"
                params.append(provider)
            if model:
                query += " AND model = ?"
                params.append(model)
            if current_only:
                query += " AND (valid_from IS NULL OR valid_from <= datetime('now'))"
                query += " AND (valid_until IS NULL OR valid_until > datetime('now'))"

            query += " ORDER BY provider, model, unit, valid_from DESC"

            cursor = conn.execute(query, params)
            rows = [dict(row) for row in cursor.fetchall()]

            return jsonify({'success': True, 'count': len(rows), 'pricing': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/pricing', methods=['POST'])
def add_pricing():
    """Add a new pricing entry, expiring any current price for the same SKU.

    Body (JSON):
        provider: Provider name (required)
        model: Model name (required)
        unit: Pricing unit (required) - e.g., 'input_tokens_1m', 'image_1024x1024'
        cost: Cost in USD (required)
        valid_from: When effective (optional, default: now)
        notes: Optional notes
    """
    data = request.get_json()

    required = ['provider', 'model', 'unit', 'cost']
    missing = [f for f in required if f not in data]
    if missing:
        return jsonify({'success': False, 'error': f'Missing required fields: {missing}'}), 400

    provider = data['provider']
    model = data['model']
    unit = data['unit']
    try:
        cost = float(data['cost'])
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid cost value: must be a number'}), 400
    valid_from = data.get('valid_from') or datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    notes = data.get('notes')

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            # Expire any current pricing for this SKU
            conn.execute("""
                UPDATE model_pricing
                SET valid_until = ?
                WHERE provider = ? AND model = ? AND unit = ?
                  AND valid_until IS NULL
            """, (valid_from, provider, model, unit))

            # Insert new pricing
            conn.execute("""
                INSERT INTO model_pricing (provider, model, unit, cost, valid_from, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (provider, model, unit, cost, valid_from, notes))

            # Invalidate pricing cache so future cost calculations use fresh data
            UsageTracker.get_default().invalidate_pricing_cache()

            return jsonify({
                'success': True,
                'message': f'Added pricing for {provider}/{model}/{unit}: ${cost}'
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/pricing/bulk', methods=['POST'])
def bulk_add_pricing():
    """Add multiple pricing entries at once.

    Body (JSON):
        entries: List of {provider, model, unit, cost, notes?}
        expire_existing: If true, expire existing prices (default: true)
    """
    data = request.get_json()
    entries = data.get('entries', [])
    expire_existing = data.get('expire_existing', True)

    if not entries:
        return jsonify({'success': False, 'error': 'No entries provided'}), 400

    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    added = 0
    errors = []

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            for entry in entries:
                try:
                    provider = entry['provider']
                    model = entry['model']
                    unit = entry['unit']
                    try:
                        cost = float(entry['cost'])
                    except (TypeError, ValueError):
                        raise ValueError(f"Invalid cost value '{entry.get('cost')}': must be a number")
                    valid_from = entry.get('valid_from') or now
                    notes = entry.get('notes')

                    if expire_existing:
                        conn.execute("""
                            UPDATE model_pricing SET valid_until = ?
                            WHERE provider = ? AND model = ? AND unit = ? AND valid_until IS NULL
                        """, (valid_from, provider, model, unit))

                    conn.execute("""
                        INSERT INTO model_pricing (provider, model, unit, cost, valid_from, notes)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (provider, model, unit, cost, valid_from, notes))
                    added += 1
                except Exception as e:
                    errors.append({'entry': entry, 'error': str(e)})

            # Invalidate pricing cache so future cost calculations use fresh data
            if added > 0:
                UsageTracker.get_default().invalidate_pricing_cache()
            return jsonify({'success': True, 'added': added, 'errors': errors})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/pricing/<int:pricing_id>', methods=['DELETE'])
def delete_pricing(pricing_id: int):
    """Delete a pricing entry by ID."""
    try:
        with sqlite3.connect(_get_db_path()) as conn:
            cursor = conn.execute("DELETE FROM model_pricing WHERE id = ?", (pricing_id,))
            if cursor.rowcount == 0:
                return jsonify({'success': False, 'error': 'Not found'}), 404
            # Invalidate pricing cache so future cost calculations use fresh data
            UsageTracker.get_default().invalidate_pricing_cache()
            return jsonify({'success': True, 'message': f'Deleted pricing entry {pricing_id}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/pricing/providers', methods=['GET'])
def list_providers():
    """List all providers with model/SKU counts."""
    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT provider, COUNT(DISTINCT model) as model_count, COUNT(*) as sku_count
                FROM model_pricing
                WHERE valid_until IS NULL OR valid_until > datetime('now')
                GROUP BY provider
                ORDER BY provider
            """)
            return jsonify({'success': True, 'providers': [dict(r) for r in cursor.fetchall()]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/pricing/models/<provider>', methods=['GET'])
def list_models_for_provider(provider: str):
    """List all models for a provider."""
    # Validate provider: alphanumeric, hyphens, underscores, max 64 chars
    if not provider or len(provider) > 64 or not re.match(r'^[\w-]+$', provider):
        return jsonify({'success': False, 'error': 'Invalid provider format'}), 400

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT DISTINCT model FROM model_pricing
                WHERE provider = ? AND (valid_until IS NULL OR valid_until > datetime('now'))
                ORDER BY model
            """, (provider,))
            return jsonify({
                'success': True,
                'provider': provider,
                'models': [r['model'] for r in cursor.fetchall()]
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# Debug Tools - Redirect to React Admin
# =============================================================================

@admin_dashboard_bp.route('/debug')
@_dev_only
def debug_page():
    """Redirect to React admin dashboard."""
    return jsonify({
        'message': 'Debug tools has moved to React UI',
        'redirect': '/?view=admin'
    })


# =============================================================================
# App Settings API
# =============================================================================

@admin_dashboard_bp.route('/api/settings')
@_dev_only
def api_get_settings():
    """Get all configurable app settings with current values and metadata.

    Returns settings for:
    - LLM_PROMPT_CAPTURE: Capture mode (disabled, all, all_except_decisions)
    - LLM_PROMPT_RETENTION_DAYS: Days to keep captures (0 = unlimited)
    - DEFAULT_PROVIDER/DEFAULT_MODEL: Default LLM for general use
    - IMAGE_PROVIDER/IMAGE_MODEL: Model for avatar generation
    - ASSISTANT_PROVIDER/ASSISTANT_MODEL: Reasoning model for experiment assistant
    """
    from ..extensions import persistence
    from core.llm.capture_config import (
        get_capture_mode, get_retention_days, get_env_defaults,
        CAPTURE_DISABLED, CAPTURE_ALL, CAPTURE_ALL_EXCEPT_DECISIONS
    )
    from core.llm.config import (
        DEFAULT_MODEL, ASSISTANT_MODEL, ASSISTANT_PROVIDER,
    )

    try:
        # Get env defaults for display
        env_defaults = get_env_defaults()

        # Get current values (DB if exists, else env)
        current_capture_mode = get_capture_mode()
        current_retention_days = get_retention_days()

        # Get DB values directly to show if overridden
        db_settings = persistence.get_all_settings()

        # System model settings - get from DB or fall back to env/defaults
        default_provider = persistence.get_setting('DEFAULT_PROVIDER', '') or 'openai'
        default_model = persistence.get_setting('DEFAULT_MODEL', '') or DEFAULT_MODEL
        image_provider = persistence.get_setting('IMAGE_PROVIDER', '') or os.environ.get('IMAGE_PROVIDER', 'openai')
        image_model = persistence.get_setting('IMAGE_MODEL', '') or os.environ.get('IMAGE_MODEL', '')
        assistant_provider = persistence.get_setting('ASSISTANT_PROVIDER', '') or ASSISTANT_PROVIDER
        assistant_model = persistence.get_setting('ASSISTANT_MODEL', '') or ASSISTANT_MODEL

        settings = {
            'LLM_PROMPT_CAPTURE': {
                'value': current_capture_mode,
                'options': [CAPTURE_DISABLED, CAPTURE_ALL, CAPTURE_ALL_EXCEPT_DECISIONS],
                'description': 'Controls which LLM calls are captured for debugging',
                'env_default': env_defaults['capture_mode'],
                'is_db_override': 'LLM_PROMPT_CAPTURE' in db_settings,
            },
            'LLM_PROMPT_RETENTION_DAYS': {
                'value': str(current_retention_days),
                'type': 'number',
                'description': 'Days to keep captures (0 = unlimited)',
                'env_default': str(env_defaults['retention_days']),
                'is_db_override': 'LLM_PROMPT_RETENTION_DAYS' in db_settings,
            },
            # System model settings
            'DEFAULT_PROVIDER': {
                'value': default_provider,
                'description': 'Default LLM provider for general use',
                'env_default': 'openai',
                'is_db_override': 'DEFAULT_PROVIDER' in db_settings,
            },
            'DEFAULT_MODEL': {
                'value': default_model,
                'description': 'Default LLM model for chat suggestions, themes, etc.',
                'env_default': DEFAULT_MODEL,
                'is_db_override': 'DEFAULT_MODEL' in db_settings,
            },
            'IMAGE_PROVIDER': {
                'value': image_provider,
                'description': 'Provider for generating AI player avatars',
                'env_default': os.environ.get('IMAGE_PROVIDER', 'openai'),
                'is_db_override': 'IMAGE_PROVIDER' in db_settings,
            },
            'IMAGE_MODEL': {
                'value': image_model,
                'description': 'Model for generating AI player avatars',
                'env_default': os.environ.get('IMAGE_MODEL', ''),
                'is_db_override': 'IMAGE_MODEL' in db_settings,
            },
            'ASSISTANT_PROVIDER': {
                'value': assistant_provider,
                'description': 'Provider for experiment design assistant (reasoning)',
                'env_default': ASSISTANT_PROVIDER,
                'is_db_override': 'ASSISTANT_PROVIDER' in db_settings,
            },
            'ASSISTANT_MODEL': {
                'value': assistant_model,
                'description': 'Reasoning model for experiment design assistant',
                'env_default': ASSISTANT_MODEL,
                'is_db_override': 'ASSISTANT_MODEL' in db_settings,
            },
        }

        return jsonify({
            'success': True,
            'settings': settings,
        })

    except Exception as e:
        logger.error(f"Error getting settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/settings', methods=['POST'])
@_dev_only
def api_update_setting():
    """Update a single app setting.

    Request body:
        key: Setting key (e.g., 'LLM_PROMPT_CAPTURE')
        value: New value
    """
    from ..extensions import persistence
    from core.llm.capture_config import CAPTURE_DISABLED, CAPTURE_ALL, CAPTURE_ALL_EXCEPT_DECISIONS

    try:
        data = request.get_json()
        if not data or 'key' not in data or 'value' not in data:
            return jsonify({'success': False, 'error': 'Missing key or value'}), 400

        key = data['key']
        value = str(data['value'])

        # Validate setting key and value
        valid_keys = {
            'LLM_PROMPT_CAPTURE', 'LLM_PROMPT_RETENTION_DAYS',
            'DEFAULT_PROVIDER', 'DEFAULT_MODEL',
            'IMAGE_PROVIDER', 'IMAGE_MODEL',
            'ASSISTANT_PROVIDER', 'ASSISTANT_MODEL',
        }
        if key not in valid_keys:
            return jsonify({'success': False, 'error': f'Unknown setting: {key}'}), 400

        # Validate values based on key
        if key == 'LLM_PROMPT_CAPTURE':
            valid_modes = [CAPTURE_DISABLED, CAPTURE_ALL, CAPTURE_ALL_EXCEPT_DECISIONS]
            if value.lower() not in valid_modes:
                return jsonify({
                    'success': False,
                    'error': f'Invalid capture mode. Must be one of: {valid_modes}'
                }), 400
            value = value.lower()

        elif key == 'LLM_PROMPT_RETENTION_DAYS':
            try:
                days = int(value)
                if days < 0:
                    return jsonify({
                        'success': False,
                        'error': 'Retention days must be >= 0'
                    }), 400
            except ValueError:
                return jsonify({
                    'success': False,
                    'error': 'Retention days must be a number'
                }), 400

        # Save the setting
        descriptions = {
            'LLM_PROMPT_CAPTURE': 'Controls which LLM calls are captured for debugging',
            'LLM_PROMPT_RETENTION_DAYS': 'Days to keep captures (0 = unlimited)',
            'DEFAULT_PROVIDER': 'Default LLM provider for general use',
            'DEFAULT_MODEL': 'Default LLM model for chat suggestions, themes, etc.',
            'IMAGE_PROVIDER': 'Provider for generating AI player avatars',
            'IMAGE_MODEL': 'Model for generating AI player avatars',
            'ASSISTANT_PROVIDER': 'Provider for experiment design assistant (reasoning)',
            'ASSISTANT_MODEL': 'Reasoning model for experiment design assistant',
        }

        success = persistence.set_setting(key, value, descriptions.get(key))

        if success:
            return jsonify({
                'success': True,
                'message': f'Setting {key} updated to {value}',
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to save setting'}), 500

    except Exception as e:
        logger.error(f"Error updating setting: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/settings/reset', methods=['POST'])
@_dev_only
def api_reset_settings():
    """Reset settings to environment variable defaults.

    Request body (optional):
        key: Specific setting to reset (if not provided, resets all)
    """
    from ..extensions import persistence

    try:
        data = request.get_json() or {}
        key = data.get('key')

        if key:
            # Reset specific setting
            valid_keys = {
                'LLM_PROMPT_CAPTURE', 'LLM_PROMPT_RETENTION_DAYS',
                'DEFAULT_PROVIDER', 'DEFAULT_MODEL',
                'IMAGE_PROVIDER', 'IMAGE_MODEL',
                'ASSISTANT_PROVIDER', 'ASSISTANT_MODEL',
            }
            if key not in valid_keys:
                return jsonify({'success': False, 'error': f'Unknown setting: {key}'}), 400

            success = persistence.delete_setting(key)
            return jsonify({
                'success': True,
                'message': f'Setting {key} reset to environment default',
                'deleted': success,
            })
        else:
            # Reset all settings
            deleted_count = 0
            all_setting_keys = [
                'LLM_PROMPT_CAPTURE', 'LLM_PROMPT_RETENTION_DAYS',
                'DEFAULT_PROVIDER', 'DEFAULT_MODEL',
                'IMAGE_PROVIDER', 'IMAGE_MODEL',
                'ASSISTANT_PROVIDER', 'ASSISTANT_MODEL',
            ]
            for k in all_setting_keys:
                if persistence.delete_setting(k):
                    deleted_count += 1

            return jsonify({
                'success': True,
                'message': f'Reset {deleted_count} settings to environment defaults',
                'deleted': deleted_count,
            })

    except Exception as e:
        logger.error(f"Error resetting settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/active-games')
@_dev_only
def api_active_games():
    """Get list of games (active in memory + recent saved games).

    Returns:
        List of games with game_id, owner_name, player names, phase, etc.
        Active games are marked with is_active=True
    """
    from ..extensions import persistence
    import json as json_module

    try:
        all_games = []
        seen_game_ids = set()

        # First, get active (in-memory) games
        for game_id in game_state_service.list_game_ids():
            game_data = game_state_service.get_game(game_id)
            if not game_data:
                continue

            state_machine = game_data.get('state_machine')
            owner_name = game_data.get('owner_name', 'Unknown')

            game_info = {
                'game_id': game_id,
                'owner_name': owner_name,
                'players': [],
                'phase': None,
                'hand_number': None,
                'is_active': True,  # In memory = active
            }

            if state_machine:
                game_state = state_machine.game_state
                if game_state:
                    game_info['phase'] = state_machine.current_phase.value if hasattr(state_machine, 'current_phase') else None
                    game_info['hand_number'] = game_state.hand_number if hasattr(game_state, 'hand_number') else None

                    # Get player names
                    if hasattr(game_state, 'players'):
                        for player in game_state.players:
                            player_info = {
                                'name': player.name,
                                'chips': player.stack,
                                'is_human': getattr(player, 'is_human', True),
                                'is_active': not player.is_folded and player.stack > 0,
                            }
                            game_info['players'].append(player_info)

            all_games.append(game_info)
            seen_game_ids.add(game_id)

        # Then, add recent saved games from database (not already in memory)
        try:
            saved_games = persistence.list_games(limit=20)
            for saved_game in saved_games:
                if saved_game.game_id in seen_game_ids:
                    continue  # Already added from memory

                game_info = {
                    'game_id': saved_game.game_id,
                    'owner_name': saved_game.owner_name or 'Unknown',
                    'players': [],
                    'phase': saved_game.phase,
                    'hand_number': None,
                    'is_active': False,  # Saved but not in memory
                    'num_players': saved_game.num_players,
                }

                # Try to extract player names from saved game state
                try:
                    state_dict = json_module.loads(saved_game.game_state_json)
                    if 'players' in state_dict:
                        for p in state_dict['players']:
                            game_info['players'].append({
                                'name': p.get('name', 'Unknown'),
                                'chips': p.get('stack', 0),
                                'is_human': p.get('is_human', False),
                                'is_active': not p.get('is_folded', False) and p.get('stack', 0) > 0,
                            })
                    if 'hand_number' in state_dict:
                        game_info['hand_number'] = state_dict['hand_number']
                except (json_module.JSONDecodeError, KeyError):
                    pass

                all_games.append(game_info)
                seen_game_ids.add(saved_game.game_id)

        except Exception as e:
            logger.warning(f"Could not load saved games: {e}")

        return jsonify({
            'success': True,
            'games': all_games,
            'count': len(all_games),
            'active_count': sum(1 for g in all_games if g.get('is_active')),
        })

    except Exception as e:
        logger.error(f"Error getting active games: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/settings/storage')
@_dev_only
def api_storage_stats():
    """Get database storage statistics.

    Returns storage breakdown by category:
    - total: Total database size
    - captures: prompt_captures, player_decision_analysis
    - api_usage: api_usage table
    - game_data: games, game_messages, hand_history, etc.
    - ai_state: ai_player_state, controller_state, opponent_models, etc.
    - config: personalities, enabled_models, model_pricing, etc.
    """
    from pathlib import Path

    try:
        db_path = _get_db_path()

        # Get total DB size
        total_bytes = Path(db_path).stat().st_size

        # Define table categories
        categories = {
            'captures': ['prompt_captures', 'player_decision_analysis'],
            'api_usage': ['api_usage'],
            'game_data': [
                'games', 'game_messages', 'hand_history', 'hand_commentary',
                'tournament_results', 'tournament_standings', 'tournament_tracker'
            ],
            'ai_state': [
                'ai_player_state', 'controller_state', 'emotional_state',
                'opponent_models', 'memorable_hands', 'personality_snapshots',
                'pressure_events', 'player_career_stats'
            ],
            'config': [
                'personalities', 'enabled_models', 'model_pricing',
                'app_settings', 'schema_version'
            ],
            'assets': ['avatar_images'],
        }

        # Build whitelist from known categories for defensive SQL
        allowed_tables = set()
        for table_list in categories.values():
            allowed_tables.update(table_list)
        # Also allow experiments table which may not be in categories
        allowed_tables.add('experiments')

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Get row counts and estimate sizes for each table
            table_stats = {}
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
            """)
            tables = [row['name'] for row in cursor.fetchall()]

            for table in tables:
                # Skip tables not in whitelist to prevent SQL injection
                if table not in allowed_tables:
                    continue

                try:
                    # Get row count - table name is validated against whitelist
                    cursor = conn.execute(f'SELECT COUNT(*) as cnt FROM "{table}"')
                    count = cursor.fetchone()['cnt']

                    # Estimate table size using page_count from dbstat if available
                    # Fallback to rough estimate based on row count
                    try:
                        cursor = conn.execute(f"""
                            SELECT SUM(pgsize) as size FROM dbstat WHERE name=?
                        """, (table,))
                        size_row = cursor.fetchone()
                        size = size_row['size'] if size_row and size_row['size'] else 0
                    except sqlite3.OperationalError:
                        # dbstat not available, use rough estimate
                        size = 0

                    table_stats[table] = {'rows': count, 'bytes': size}
                except sqlite3.OperationalError:
                    table_stats[table] = {'rows': 0, 'bytes': 0}

            # Aggregate by category
            category_stats = {}
            categorized_tables = set()

            for category, table_list in categories.items():
                rows = 0
                bytes_est = 0
                for table in table_list:
                    if table in table_stats:
                        rows += table_stats[table]['rows']
                        bytes_est += table_stats[table]['bytes']
                        categorized_tables.add(table)
                category_stats[category] = {'rows': rows, 'bytes': bytes_est}

            # Add 'other' category for uncategorized tables
            other_rows = 0
            other_bytes = 0
            for table, stats in table_stats.items():
                if table not in categorized_tables:
                    other_rows += stats['rows']
                    other_bytes += stats['bytes']
            if other_rows > 0 or other_bytes > 0:
                category_stats['other'] = {'rows': other_rows, 'bytes': other_bytes}

            # Calculate percentages based on total bytes
            # If dbstat not available, estimate from row proportions
            total_tracked_bytes = sum(cat['bytes'] for cat in category_stats.values())
            if total_tracked_bytes == 0:
                # Estimate percentages from row counts
                total_rows = sum(cat['rows'] for cat in category_stats.values())
                for category in category_stats:
                    if total_rows > 0:
                        pct = (category_stats[category]['rows'] / total_rows) * 100
                        category_stats[category]['bytes'] = int(total_bytes * pct / 100)
                    category_stats[category]['percentage'] = round(
                        (category_stats[category]['rows'] / total_rows * 100) if total_rows > 0 else 0, 1
                    )
            else:
                for category in category_stats:
                    category_stats[category]['percentage'] = round(
                        (category_stats[category]['bytes'] / total_bytes * 100), 1
                    )

            return jsonify({
                'success': True,
                'storage': {
                    'total_bytes': total_bytes,
                    'total_mb': round(total_bytes / 1024 / 1024, 2),
                    'categories': category_stats,
                    'tables': table_stats,
                }
            })

    except Exception as e:
        logger.error(f"Error getting storage stats: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# The following HTML was removed - all admin pages now use React UI
_LEGACY_DEBUG_HTML = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Debug Tools - Admin Dashboard</title>
        <style>
            * {{ box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #1a1a2e;
                color: #eee;
                margin: 0;
                padding: 0;
            }}
            .sidebar {{
                width: 200px;
                background: #16213e;
                position: fixed;
                height: 100%;
                padding: 20px;
            }}
            .sidebar h2 {{
                color: #00d4ff;
                margin: 0 0 30px 0;
                font-size: 1.2em;
            }}
            .sidebar nav a {{
                display: block;
                color: #aaa;
                text-decoration: none;
                padding: 10px 15px;
                margin: 5px 0;
                border-radius: 6px;
                transition: all 0.2s;
            }}
            .sidebar nav a:hover {{
                background: #0f3460;
                color: #eee;
            }}
            .sidebar nav a.active {{
                background: #4ecca3;
                color: #1a1a2e;
            }}
            .content {{
                margin-left: 220px;
                padding: 30px;
            }}
            h1 {{
                color: #00d4ff;
                margin: 0 0 10px 0;
            }}
            .subtitle {{
                color: #888;
                margin-bottom: 30px;
            }}
            h2 {{
                color: #ff6b6b;
                margin-top: 30px;
                font-size: 1.2em;
            }}
            .section {{
                background: #16213e;
                padding: 20px;
                border-radius: 8px;
                margin: 15px 0;
            }}
            .endpoint {{
                margin: 10px 0;
                padding: 15px;
                background: #0f3460;
                border-radius: 4px;
            }}
            .method {{
                color: #ff9f1c;
                font-weight: bold;
                font-family: monospace;
            }}
            .url {{
                color: #4ecca3;
                font-family: monospace;
            }}
            .desc {{
                color: #aaa;
                font-size: 0.9em;
                margin: 5px 0;
            }}
            input, select {{
                background: #0f3460;
                color: #eee;
                border: 1px solid #4ecca3;
                padding: 8px 12px;
                border-radius: 4px;
                margin: 5px;
            }}
            button {{
                background: #4ecca3;
                color: #1a1a2e;
                border: none;
                padding: 10px 20px;
                border-radius: 4px;
                cursor: pointer;
                font-weight: bold;
            }}
            button:hover {{
                background: #3db892;
            }}
            pre {{
                background: #0f3460;
                padding: 15px;
                border-radius: 4px;
                overflow-x: auto;
                font-family: monospace;
                font-size: 0.85em;
            }}
            .game-id {{
                background: #0f3460;
                padding: 5px 10px;
                border-radius: 4px;
                margin: 5px;
                display: inline-block;
                font-family: monospace;
            }}
            a {{
                color: #4ecca3;
            }}
        </style>
    </head>
    <body>
        <div class="sidebar">
            <h2>Admin Dashboard</h2>
            <nav>
                <a href="/admin/">Dashboard</a>
                <a href="/admin/costs">Cost Analysis</a>
                <a href="/admin/performance">Performance</a>
                <a href="/admin/prompts">Prompts</a>
                <a href="/admin/models">Models</a>
                <a href="/admin/pricing">Pricing</a>
                <a href="/admin/debug" class="active">Debug Tools</a>
            </nav>
        </div>
        <div class="content">
            <h1>Debug Tools</h1>
            <p class="subtitle">Game debugging and AI system inspection tools</p>

            <div class="section">
                <h2>Active Games</h2>
                <div style="margin: 10px 0;">
                    {games_html}
                </div>
                <p><a href="/games">View saved games</a></p>
            </div>

            <div class="section">
                <h2>Tilt System Debug</h2>
                <p class="desc">Test the tilt modifier system that affects AI decision-making</p>

                <div class="endpoint">
                    <span class="method">GET</span>
                    <span class="url">/api/game/{{game_id}}/tilt-debug</span>
                    <p class="desc">View tilt state for all AI players</p>
                    <input type="text" id="tilt-game-id" placeholder="game_id" style="width: 300px;">
                    <button onclick="fetchTilt()">Fetch Tilt States</button>
                </div>

                <div class="endpoint">
                    <span class="method">POST</span>
                    <span class="url">/api/game/{{game_id}}/tilt-debug/{{player_name}}</span>
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
                    <span class="url">/api/game/{{game_id}}/memory-debug</span>
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
                    <span class="url">/api/game/{{game_id}}/elasticity</span>
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
                    <span class="url">/api/game/{{game_id}}/pressure-stats</span>
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
                    <span class="url">/api/game/{{game_id}}/diagnostic</span>
                    <p class="desc">Full game diagnostic info</p>
                    <input type="text" id="diag-game-id" placeholder="game_id" style="width: 300px;">
                    <button onclick="fetchDiagnostic()">Fetch Diagnostic</button>
                </div>
                <pre id="diag-result">Results will appear here...</pre>
            </div>
        </div>

        <script>
            async function fetchJson(url, options = {{}}) {{
                try {{
                    const resp = await fetch(url, options);
                    return await resp.json();
                }} catch (e) {{
                    return {{error: e.message}};
                }}
            }}

            async function fetchTilt() {{
                const gameId = document.getElementById('tilt-game-id').value;
                const result = await fetchJson(`/api/game/${{gameId}}/tilt-debug`);
                document.getElementById('tilt-result').textContent = JSON.stringify(result, null, 2);
            }}

            async function setTilt() {{
                const gameId = document.getElementById('set-tilt-game-id').value;
                const player = encodeURIComponent(document.getElementById('set-tilt-player').value);
                const data = {{
                    tilt_level: parseFloat(document.getElementById('tilt-level').value),
                    tilt_source: document.getElementById('tilt-source').value,
                    nemesis: document.getElementById('tilt-nemesis').value || null
                }};
                const result = await fetchJson(`/api/game/${{gameId}}/tilt-debug/${{player}}`, {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify(data)
                }});
                document.getElementById('tilt-result').textContent = JSON.stringify(result, null, 2);
            }}

            async function fetchMemory() {{
                const gameId = document.getElementById('memory-game-id').value;
                const result = await fetchJson(`/api/game/${{gameId}}/memory-debug`);
                document.getElementById('memory-result').textContent = JSON.stringify(result, null, 2);
            }}

            async function fetchElasticity() {{
                const gameId = document.getElementById('elasticity-game-id').value;
                const result = await fetchJson(`/api/game/${{gameId}}/elasticity`);
                document.getElementById('elasticity-result').textContent = JSON.stringify(result, null, 2);
            }}

            async function fetchPressure() {{
                const gameId = document.getElementById('pressure-game-id').value;
                const result = await fetchJson(`/api/game/${{gameId}}/pressure-stats`);
                document.getElementById('pressure-result').textContent = JSON.stringify(result, null, 2);
            }}

            async function fetchDiagnostic() {{
                const gameId = document.getElementById('diag-game-id').value;
                const result = await fetchJson(`/api/game/${{gameId}}/diagnostic`);
                document.getElementById('diag-result').textContent = JSON.stringify(result, null, 2);
            }}
        </script>
    </body>
    </html>
    '''
