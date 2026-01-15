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
    """Decorator to require admin authentication and restrict to development mode.

    Security layers:
    1. Checks development mode (fails in production unless explicitly enabled)
    2. Requires ADMIN_TOKEN authentication when token is configured

    Token can be provided via:
    - Authorization: Bearer <token> header
    - ?admin_token=<token> query parameter
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        # Check development mode
        if not config.is_development:
            return jsonify({'error': 'Admin dashboard only available in development mode'}), 403

        # Check authentication
        is_authenticated, error_msg = _check_admin_auth()
        if not is_authenticated:
            # Return 401 for API requests, redirect hint for browser requests
            if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
                return jsonify({'error': error_msg}), 401
            return jsonify({
                'error': error_msg,
                'hint': 'Add ?admin_token=YOUR_TOKEN to the URL or set Authorization: Bearer YOUR_TOKEN header'
            }), 401

        return f(*args, **kwargs)
    return decorated


# Keep old decorator name as alias for backwards compatibility
_dev_only = _admin_required


# =============================================================================
# Dashboard
# =============================================================================

@admin_dashboard_bp.route('/')
@_dev_only
def dashboard():
    """Main analytics dashboard."""
    range_param = request.args.get('range', '7d')
    date_modifier = _get_date_modifier(range_param)

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            # Summary metrics (parameterized query to prevent SQL injection)
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

            # Cost by provider (parameterized query)
            cursor = conn.execute("""
                SELECT
                    provider,
                    COUNT(*) as calls,
                    COALESCE(SUM(estimated_cost), 0) as cost
                FROM api_usage
                WHERE created_at >= datetime('now', ?)
                GROUP BY provider
                ORDER BY cost DESC
            """, (date_modifier,))
            cost_by_provider = [dict(row) for row in cursor.fetchall()]

            # Calls by type (parameterized query)
            cursor = conn.execute("""
                SELECT
                    call_type,
                    COUNT(*) as calls,
                    COALESCE(SUM(estimated_cost), 0) as cost
                FROM api_usage
                WHERE created_at >= datetime('now', ?)
                GROUP BY call_type
                ORDER BY calls DESC
            """, (date_modifier,))
            calls_by_type = [dict(row) for row in cursor.fetchall()]

        return _render_dashboard(summary, cost_by_provider, calls_by_type, range_param)

    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        return _render_dashboard_error(str(e))


def _render_dashboard(summary, cost_by_provider, calls_by_type, range_param):
    """Render the dashboard HTML."""

    # Provider colors for charts
    provider_colors = {
        'openai': '#10b981',
        'anthropic': '#f59e0b',
        'groq': '#3b82f6',
        'deepseek': '#8b5cf6',
        'mistral': '#ef4444',
        'google': '#06b6d4',
        'xai': '#ec4899',
    }

    # Build provider data for chart
    provider_labels = [p['provider'] or 'unknown' for p in cost_by_provider]
    provider_costs = [p['cost'] for p in cost_by_provider]
    provider_colors_list = [provider_colors.get(p, '#6b7280') for p in provider_labels]

    # Build call type data for chart
    type_labels = [t['call_type'] or 'unknown' for t in calls_by_type]
    type_counts = [t['calls'] for t in calls_by_type]

    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Admin Dashboard Dashboard</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
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
            .date-selector {{
                margin-bottom: 30px;
            }}
            .date-selector button {{
                background: #0f3460;
                color: #eee;
                border: none;
                padding: 8px 16px;
                margin-right: 10px;
                border-radius: 4px;
                cursor: pointer;
            }}
            .date-selector button.active {{
                background: #4ecca3;
                color: #1a1a2e;
            }}
            .metrics-grid {{
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 20px;
                margin-bottom: 30px;
            }}
            .metric-card {{
                background: #16213e;
                padding: 20px;
                border-radius: 8px;
                text-align: center;
            }}
            .metric-value {{
                font-size: 2em;
                font-weight: bold;
                color: #4ecca3;
            }}
            .metric-label {{
                color: #888;
                margin-top: 5px;
            }}
            .charts-row {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 20px;
                margin-bottom: 30px;
            }}
            .chart-card {{
                background: #16213e;
                padding: 20px;
                border-radius: 8px;
            }}
            .chart-card h3 {{
                color: #eee;
                margin: 0 0 15px 0;
            }}
            .chart-container {{
                height: 300px;
            }}
            .table-card {{
                background: #16213e;
                padding: 20px;
                border-radius: 8px;
                margin-bottom: 20px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
            }}
            th, td {{
                padding: 12px;
                text-align: left;
                border-bottom: 1px solid #0f3460;
            }}
            th {{
                color: #888;
                font-weight: normal;
            }}
            .cost {{ color: #4ecca3; }}
            .error {{ color: #ef4444; }}
        </style>
    </head>
    <body>
        <div class="sidebar">
            <h2>Admin Dashboard</h2>
            <nav>
                <a href="/admin/" class="active">Dashboard</a>
                <a href="/admin/costs">Cost Analysis</a>
                <a href="/admin/performance">Performance</a>
                <a href="/admin/prompts">Prompts</a>
                <a href="/admin/models">Models</a>
                <a href="/admin/pricing">Pricing</a>
                <a href="/admin/debug">Debug Tools</a>
            </nav>
        </div>

        <div class="content">
            <h1>Admin Dashboard Dashboard</h1>
            <p class="subtitle">Monitor API usage, costs, and performance across providers</p>

            <div class="date-selector">
                <button onclick="setRange('24h')" class="{'active' if range_param == '24h' else ''}">24 hours</button>
                <button onclick="setRange('7d')" class="{'active' if range_param == '7d' else ''}">7 days</button>
                <button onclick="setRange('30d')" class="{'active' if range_param == '30d' else ''}">30 days</button>
                <button onclick="setRange('all')" class="{'active' if range_param == 'all' else ''}">All time</button>
            </div>

            <div class="metrics-grid">
                <div class="metric-card">
                    <div class="metric-value">{summary['total_calls']:,}</div>
                    <div class="metric-label">API Calls</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value cost">${summary['total_cost']:.4f}</div>
                    <div class="metric-label">Total Cost</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value">{summary['avg_latency']:.0f}ms</div>
                    <div class="metric-label">Avg Latency</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value {'error' if summary['error_rate'] > 5 else ''}">{summary['error_rate']:.1f}%</div>
                    <div class="metric-label">Error Rate</div>
                </div>
            </div>

            <div class="charts-row">
                <div class="chart-card">
                    <h3>Cost by Provider</h3>
                    <div class="chart-container">
                        <canvas id="providerChart"></canvas>
                    </div>
                </div>
                <div class="chart-card">
                    <h3>Calls by Type</h3>
                    <div class="chart-container">
                        <canvas id="typeChart"></canvas>
                    </div>
                </div>
            </div>

            <div class="table-card">
                <h3>Provider Breakdown</h3>
                <table>
                    <thead>
                        <tr>
                            <th>Provider</th>
                            <th>Calls</th>
                            <th>Cost</th>
                            <th>Avg Cost/Call</th>
                        </tr>
                    </thead>
                    <tbody>
    '''

    for p in cost_by_provider:
        avg_cost = p['cost'] / p['calls'] if p['calls'] > 0 else 0
        html += f'''
                        <tr>
                            <td>{p['provider'] or 'unknown'}</td>
                            <td>{p['calls']:,}</td>
                            <td class="cost">${p['cost']:.4f}</td>
                            <td class="cost">${avg_cost:.6f}</td>
                        </tr>
        '''

    html += f'''
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            function setRange(range) {{
                window.location.href = '/admin/?range=' + range;
            }}

            // Provider pie chart
            new Chart(document.getElementById('providerChart'), {{
                type: 'doughnut',
                data: {{
                    labels: {provider_labels},
                    datasets: [{{
                        data: {provider_costs},
                        backgroundColor: {provider_colors_list}
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        legend: {{
                            position: 'right',
                            labels: {{ color: '#eee' }}
                        }},
                        tooltip: {{
                            callbacks: {{
                                label: function(ctx) {{
                                    return ctx.label + ': $' + ctx.raw.toFixed(4);
                                }}
                            }}
                        }}
                    }}
                }}
            }});

            // Call type bar chart
            new Chart(document.getElementById('typeChart'), {{
                type: 'bar',
                data: {{
                    labels: {type_labels},
                    datasets: [{{
                        label: 'Calls',
                        data: {type_counts},
                        backgroundColor: '#4ecca3'
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    indexAxis: 'y',
                    plugins: {{
                        legend: {{ display: false }}
                    }},
                    scales: {{
                        x: {{
                            ticks: {{ color: '#888' }},
                            grid: {{ color: '#0f3460' }}
                        }},
                        y: {{
                            ticks: {{ color: '#eee' }},
                            grid: {{ display: false }}
                        }}
                    }}
                }}
            }});
        </script>
    </body>
    </html>
    '''

    return html


def _render_dashboard_error(error: str):
    """Render error page."""
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Admin Dashboard - Error</title>
        <style>
            body {{ font-family: sans-serif; background: #1a1a2e; color: #eee; padding: 40px; }}
            .error {{ background: #ef4444; padding: 20px; border-radius: 8px; }}
        </style>
    </head>
    <body>
        <h1>Analytics Error</h1>
        <div class="error">{error}</div>
        <p><a href="/admin/" style="color: #4ecca3;">Try again</a></p>
    </body>
    </html>
    '''


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
# Cost Analysis
# =============================================================================

@admin_dashboard_bp.route('/costs')
@_dev_only
def costs():
    """Cost analysis page with detailed breakdowns."""
    range_param = request.args.get('range', '7d')
    date_modifier = _get_date_modifier(range_param)

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            # Cost by model (parameterized query)
            cursor = conn.execute("""
                SELECT
                    provider,
                    model,
                    COUNT(*) as calls,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    COALESCE(SUM(estimated_cost), 0) as cost
                FROM api_usage
                WHERE created_at >= datetime('now', ?)
                GROUP BY provider, model
                ORDER BY cost DESC
            """, (date_modifier,))
            by_model = [dict(row) for row in cursor.fetchall()]

            # Cost by call type (parameterized query)
            cursor = conn.execute("""
                SELECT
                    call_type,
                    COUNT(*) as calls,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    COALESCE(SUM(estimated_cost), 0) as cost
                FROM api_usage
                WHERE created_at >= datetime('now', ?)
                GROUP BY call_type
                ORDER BY cost DESC
            """, (date_modifier,))
            by_type = [dict(row) for row in cursor.fetchall()]

            # Daily time series (parameterized query)
            cursor = conn.execute("""
                SELECT
                    DATE(created_at) as date,
                    provider,
                    COALESCE(SUM(estimated_cost), 0) as cost,
                    COUNT(*) as calls
                FROM api_usage
                WHERE created_at >= datetime('now', ?)
                GROUP BY DATE(created_at), provider
                ORDER BY date
            """, (date_modifier,))
            time_series = [dict(row) for row in cursor.fetchall()]

        return _render_costs(by_model, by_type, time_series, range_param)

    except Exception as e:
        logger.error(f"Costs error: {e}")
        return _render_dashboard_error(str(e))


def _render_costs(by_model, by_type, time_series, range_param):
    """Render costs page HTML."""

    # Build time series data for chart
    dates = sorted(set(t['date'] for t in time_series))
    providers = sorted(set(t['provider'] for t in time_series if t['provider']))

    provider_colors = {
        'openai': '#10b981', 'anthropic': '#f59e0b', 'groq': '#3b82f6',
        'deepseek': '#8b5cf6', 'mistral': '#ef4444', 'google': '#06b6d4', 'xai': '#ec4899',
    }

    # Build datasets for stacked bar chart
    datasets_js = []
    for provider in providers:
        data = []
        for date in dates:
            match = next((t for t in time_series if t['date'] == date and t['provider'] == provider), None)
            data.append(match['cost'] if match else 0)
        datasets_js.append({
            'label': provider,
            'data': data,
            'backgroundColor': provider_colors.get(provider, '#6b7280')
        })

    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Cost Analysis - Admin Dashboard</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            * {{ box-sizing: border-box; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #eee; margin: 0; }}
            .sidebar {{ width: 200px; background: #16213e; position: fixed; height: 100%; padding: 20px; }}
            .sidebar h2 {{ color: #00d4ff; margin: 0 0 30px 0; font-size: 1.2em; }}
            .sidebar nav a {{ display: block; color: #aaa; text-decoration: none; padding: 10px 15px; margin: 5px 0; border-radius: 6px; }}
            .sidebar nav a:hover {{ background: #0f3460; color: #eee; }}
            .sidebar nav a.active {{ background: #4ecca3; color: #1a1a2e; }}
            .content {{ margin-left: 220px; padding: 30px; }}
            h1 {{ color: #00d4ff; margin: 0 0 10px 0; }}
            .subtitle {{ color: #888; margin-bottom: 30px; }}
            .date-selector {{ margin-bottom: 30px; }}
            .date-selector button {{ background: #0f3460; color: #eee; border: none; padding: 8px 16px; margin-right: 10px; border-radius: 4px; cursor: pointer; }}
            .date-selector button.active {{ background: #4ecca3; color: #1a1a2e; }}
            .card {{ background: #16213e; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
            .card h3 {{ margin: 0 0 15px 0; color: #eee; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #0f3460; }}
            th {{ color: #888; font-weight: normal; }}
            .cost {{ color: #4ecca3; }}
            .chart-container {{ height: 300px; }}
        </style>
    </head>
    <body>
        <div class="sidebar">
            <h2>Admin Dashboard</h2>
            <nav>
                <a href="/admin/">Dashboard</a>
                <a href="/admin/costs" class="active">Cost Analysis</a>
                <a href="/admin/performance">Performance</a>
                <a href="/admin/prompts">Prompts</a>
                <a href="/admin/models">Models</a>
                <a href="/admin/pricing">Pricing</a>
                <a href="/admin/debug">Debug Tools</a>
            </nav>
        </div>
        <div class="content">
            <h1>Cost Analysis</h1>
            <p class="subtitle">Breakdown of API costs by model and call type</p>

            <div class="date-selector">
                <button onclick="setRange('24h')" class="{'active' if range_param == '24h' else ''}">24 hours</button>
                <button onclick="setRange('7d')" class="{'active' if range_param == '7d' else ''}">7 days</button>
                <button onclick="setRange('30d')" class="{'active' if range_param == '30d' else ''}">30 days</button>
                <button onclick="setRange('all')" class="{'active' if range_param == 'all' else ''}">All time</button>
            </div>

            <div class="card">
                <h3>Cost Over Time</h3>
                <div class="chart-container">
                    <canvas id="timeChart"></canvas>
                </div>
            </div>

            <div class="card">
                <h3>Cost by Model</h3>
                <table>
                    <thead><tr><th>Provider</th><th>Model</th><th>Calls</th><th>Input Tokens</th><th>Output Tokens</th><th>Cost</th></tr></thead>
                    <tbody>
    '''

    for m in by_model:
        html += f'''
                        <tr>
                            <td>{m['provider'] or 'unknown'}</td>
                            <td>{m['model'] or 'unknown'}</td>
                            <td>{m['calls']:,}</td>
                            <td>{(m['input_tokens'] or 0):,}</td>
                            <td>{(m['output_tokens'] or 0):,}</td>
                            <td class="cost">${m['cost']:.4f}</td>
                        </tr>
        '''

    html += '''
                    </tbody>
                </table>
            </div>

            <div class="card">
                <h3>Cost by Call Type</h3>
                <table>
                    <thead><tr><th>Call Type</th><th>Calls</th><th>Input Tokens</th><th>Output Tokens</th><th>Cost</th></tr></thead>
                    <tbody>
    '''

    for t in by_type:
        html += f'''
                        <tr>
                            <td>{t['call_type'] or 'unknown'}</td>
                            <td>{t['calls']:,}</td>
                            <td>{(t['input_tokens'] or 0):,}</td>
                            <td>{(t['output_tokens'] or 0):,}</td>
                            <td class="cost">${t['cost']:.4f}</td>
                        </tr>
        '''

    html += f'''
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            function setRange(range) {{ window.location.href = '/admin/costs?range=' + range; }}

            new Chart(document.getElementById('timeChart'), {{
                type: 'bar',
                data: {{
                    labels: {list(dates)},
                    datasets: {datasets_js}
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {{
                        x: {{ stacked: true, ticks: {{ color: '#888' }}, grid: {{ color: '#0f3460' }} }},
                        y: {{ stacked: true, ticks: {{ color: '#888', callback: v => '$' + v.toFixed(2) }}, grid: {{ color: '#0f3460' }} }}
                    }},
                    plugins: {{ legend: {{ labels: {{ color: '#eee' }} }} }}
                }}
            }});
        </script>
    </body>
    </html>
    '''
    return html


# =============================================================================
# Performance Metrics
# =============================================================================

@admin_dashboard_bp.route('/performance')
@_dev_only
def performance():
    """Performance metrics page with latency and error analysis."""
    range_param = request.args.get('range', '7d')
    date_modifier = _get_date_modifier(range_param)

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            # Latency by provider (parameterized query)
            cursor = conn.execute("""
                SELECT provider, latency_ms
                FROM api_usage
                WHERE created_at >= datetime('now', ?)
                  AND status = 'ok'
                  AND latency_ms IS NOT NULL
            """, (date_modifier,))
            latency_data = {}
            for row in cursor.fetchall():
                provider = row['provider'] or 'unknown'
                if provider not in latency_data:
                    latency_data[provider] = []
                latency_data[provider].append(row['latency_ms'])

            # Calculate percentiles (pure Python, no numpy needed)
            def percentile(data, p):
                """Calculate percentile without numpy."""
                if not data:
                    return 0
                sorted_data = sorted(data)
                k = (len(sorted_data) - 1) * p / 100
                f = int(k)
                c = f + 1 if f + 1 < len(sorted_data) else f
                return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)

            latency_stats = []
            for provider, latencies in latency_data.items():
                if latencies:
                    latency_stats.append({
                        'provider': provider,
                        'count': len(latencies),
                        'p50': percentile(latencies, 50),
                        'p90': percentile(latencies, 90),
                        'p95': percentile(latencies, 95),
                        'p99': percentile(latencies, 99),
                    })

            # Error rates by provider/model (parameterized query)
            cursor = conn.execute("""
                SELECT
                    provider,
                    model,
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
                    COALESCE(SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0), 0) as error_rate
                FROM api_usage
                WHERE created_at >= datetime('now', ?)
                GROUP BY provider, model
                ORDER BY error_rate DESC
            """, (date_modifier,))
            error_rates = [dict(row) for row in cursor.fetchall()]

            # Token efficiency (parameterized query)
            cursor = conn.execute("""
                SELECT
                    provider,
                    AVG(CAST(output_tokens AS FLOAT) / NULLIF(input_tokens, 0)) as output_ratio,
                    SUM(cached_tokens) * 100.0 / NULLIF(SUM(input_tokens), 0) as cache_rate,
                    COUNT(*) as calls
                FROM api_usage
                WHERE created_at >= datetime('now', ?)
                  AND status = 'ok'
                  AND input_tokens > 0
                GROUP BY provider
            """, (date_modifier,))
            efficiency = [dict(row) for row in cursor.fetchall()]

        return _render_performance(latency_stats, error_rates, efficiency, range_param)

    except Exception as e:
        logger.error(f"Performance error: {e}")
        return _render_dashboard_error(str(e))


def _render_performance(latency_stats, error_rates, efficiency, range_param):
    """Render performance page HTML."""

    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Performance - Admin Dashboard</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            * {{ box-sizing: border-box; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #eee; margin: 0; }}
            .sidebar {{ width: 200px; background: #16213e; position: fixed; height: 100%; padding: 20px; }}
            .sidebar h2 {{ color: #00d4ff; margin: 0 0 30px 0; font-size: 1.2em; }}
            .sidebar nav a {{ display: block; color: #aaa; text-decoration: none; padding: 10px 15px; margin: 5px 0; border-radius: 6px; }}
            .sidebar nav a:hover {{ background: #0f3460; color: #eee; }}
            .sidebar nav a.active {{ background: #4ecca3; color: #1a1a2e; }}
            .content {{ margin-left: 220px; padding: 30px; }}
            h1 {{ color: #00d4ff; margin: 0 0 10px 0; }}
            .subtitle {{ color: #888; margin-bottom: 30px; }}
            .date-selector {{ margin-bottom: 30px; }}
            .date-selector button {{ background: #0f3460; color: #eee; border: none; padding: 8px 16px; margin-right: 10px; border-radius: 4px; cursor: pointer; }}
            .date-selector button.active {{ background: #4ecca3; color: #1a1a2e; }}
            .card {{ background: #16213e; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
            .card h3 {{ margin: 0 0 15px 0; color: #eee; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #0f3460; }}
            th {{ color: #888; font-weight: normal; }}
            .good {{ color: #4ecca3; }}
            .bad {{ color: #ef4444; }}
            .warn {{ color: #f59e0b; }}
        </style>
    </head>
    <body>
        <div class="sidebar">
            <h2>Admin Dashboard</h2>
            <nav>
                <a href="/admin/">Dashboard</a>
                <a href="/admin/costs">Cost Analysis</a>
                <a href="/admin/performance" class="active">Performance</a>
                <a href="/admin/prompts">Prompts</a>
                <a href="/admin/models">Models</a>
                <a href="/admin/pricing">Pricing</a>
                <a href="/admin/debug">Debug Tools</a>
            </nav>
        </div>
        <div class="content">
            <h1>Performance Metrics</h1>
            <p class="subtitle">Latency percentiles, error rates, and token efficiency</p>

            <div class="date-selector">
                <button onclick="setRange('24h')" class="{'active' if range_param == '24h' else ''}">24 hours</button>
                <button onclick="setRange('7d')" class="{'active' if range_param == '7d' else ''}">7 days</button>
                <button onclick="setRange('30d')" class="{'active' if range_param == '30d' else ''}">30 days</button>
                <button onclick="setRange('all')" class="{'active' if range_param == 'all' else ''}">All time</button>
            </div>

            <div class="card">
                <h3>Latency Percentiles by Provider</h3>
                <table>
                    <thead><tr><th>Provider</th><th>Calls</th><th>P50</th><th>P90</th><th>P95</th><th>P99</th></tr></thead>
                    <tbody>
    '''

    for s in latency_stats:
        html += f'''
                        <tr>
                            <td>{s['provider']}</td>
                            <td>{s['count']:,}</td>
                            <td>{s['p50']:.0f}ms</td>
                            <td>{s['p90']:.0f}ms</td>
                            <td class="{'warn' if s['p95'] > 10000 else ''}">{s['p95']:.0f}ms</td>
                            <td class="{'bad' if s['p99'] > 20000 else 'warn' if s['p99'] > 10000 else ''}">{s['p99']:.0f}ms</td>
                        </tr>
        '''

    html += '''
                    </tbody>
                </table>
            </div>

            <div class="card">
                <h3>Error Rates by Model</h3>
                <table>
                    <thead><tr><th>Provider</th><th>Model</th><th>Total Calls</th><th>Errors</th><th>Error Rate</th></tr></thead>
                    <tbody>
    '''

    for e in error_rates[:20]:  # Limit to top 20
        rate_class = 'bad' if e['error_rate'] > 5 else 'warn' if e['error_rate'] > 1 else 'good'
        html += f'''
                        <tr>
                            <td>{e['provider'] or 'unknown'}</td>
                            <td>{e['model'] or 'unknown'}</td>
                            <td>{e['total']:,}</td>
                            <td>{e['errors']}</td>
                            <td class="{rate_class}">{e['error_rate']:.1f}%</td>
                        </tr>
        '''

    html += '''
                    </tbody>
                </table>
            </div>

            <div class="card">
                <h3>Token Efficiency by Provider</h3>
                <table>
                    <thead><tr><th>Provider</th><th>Calls</th><th>Output/Input Ratio</th><th>Cache Hit Rate</th></tr></thead>
                    <tbody>
    '''

    for e in efficiency:
        cache_class = 'good' if (e['cache_rate'] or 0) > 50 else ''
        html += f'''
                        <tr>
                            <td>{e['provider'] or 'unknown'}</td>
                            <td>{e['calls']:,}</td>
                            <td>{(e['output_ratio'] or 0):.2f}x</td>
                            <td class="{cache_class}">{(e['cache_rate'] or 0):.1f}%</td>
                        </tr>
        '''

    html += '''
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            function setRange(range) { window.location.href = '/admin/performance?range=' + range; }
        </script>
    </body>
    </html>
    '''
    return html


# =============================================================================
# Prompt Viewer
# =============================================================================

@admin_dashboard_bp.route('/prompts')
@_dev_only
def prompts():
    """Prompt viewer with filtering and pagination."""
    range_param = request.args.get('range', '7d')
    call_type = request.args.get('call_type', '')
    provider = request.args.get('provider', '')
    page = int(request.args.get('page', 1))
    per_page = 50

    date_modifier = _get_date_modifier(range_param)

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            # Build query with filters (all parameterized to prevent SQL injection)
            where_clauses = ["created_at >= datetime('now', ?)"]
            params = [date_modifier]

            if call_type:
                where_clauses.append("call_type = ?")
                params.append(call_type)
            if provider:
                where_clauses.append("provider = ?")
                params.append(provider)

            where_sql = " AND ".join(where_clauses)

            # Count total
            cursor = conn.execute(f"SELECT COUNT(*) FROM api_usage WHERE {where_sql}", params)
            total = cursor.fetchone()[0]

            # Get page of results
            offset = (page - 1) * per_page
            cursor = conn.execute(f"""
                SELECT
                    id, created_at, provider, model, call_type, player_name, game_id,
                    input_tokens, output_tokens, latency_ms, estimated_cost, status
                FROM api_usage
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """, params + [per_page, offset])
            rows = [dict(row) for row in cursor.fetchall()]

            # Get distinct call types for filter
            cursor = conn.execute("SELECT DISTINCT call_type FROM api_usage ORDER BY call_type")
            call_types = [r[0] for r in cursor.fetchall() if r[0]]

            # Get distinct providers for filter
            cursor = conn.execute("SELECT DISTINCT provider FROM api_usage ORDER BY provider")
            providers = [r[0] for r in cursor.fetchall() if r[0]]

        return _render_prompts(rows, total, page, per_page, call_types, providers, range_param, call_type, provider)

    except Exception as e:
        logger.error(f"Prompts error: {e}")
        return _render_dashboard_error(str(e))


def _render_prompts(rows, total, page, per_page, call_types, providers, range_param, selected_type, selected_provider):
    """Render prompts page HTML."""
    total_pages = (total + per_page - 1) // per_page

    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Prompts - Admin Dashboard</title>
        <style>
            * {{ box-sizing: border-box; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #eee; margin: 0; }}
            .sidebar {{ width: 200px; background: #16213e; position: fixed; height: 100%; padding: 20px; }}
            .sidebar h2 {{ color: #00d4ff; margin: 0 0 30px 0; font-size: 1.2em; }}
            .sidebar nav a {{ display: block; color: #aaa; text-decoration: none; padding: 10px 15px; margin: 5px 0; border-radius: 6px; }}
            .sidebar nav a:hover {{ background: #0f3460; color: #eee; }}
            .sidebar nav a.active {{ background: #4ecca3; color: #1a1a2e; }}
            .content {{ margin-left: 220px; padding: 30px; }}
            h1 {{ color: #00d4ff; margin: 0 0 10px 0; }}
            .subtitle {{ color: #888; margin-bottom: 20px; }}
            .filters {{ margin-bottom: 20px; display: flex; gap: 15px; flex-wrap: wrap; }}
            .filters select, .filters button {{ background: #0f3460; color: #eee; border: 1px solid #4ecca3; padding: 8px 12px; border-radius: 4px; }}
            .card {{ background: #16213e; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
            table {{ width: 100%; border-collapse: collapse; font-size: 0.9em; }}
            th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #0f3460; }}
            th {{ color: #888; font-weight: normal; }}
            .cost {{ color: #4ecca3; }}
            .error {{ color: #ef4444; }}
            .pagination {{ display: flex; gap: 10px; align-items: center; margin-top: 20px; }}
            .pagination a, .pagination span {{ padding: 8px 12px; background: #0f3460; border-radius: 4px; text-decoration: none; color: #eee; }}
            .pagination a:hover {{ background: #4ecca3; color: #1a1a2e; }}
            .pagination .current {{ background: #4ecca3; color: #1a1a2e; }}
        </style>
    </head>
    <body>
        <div class="sidebar">
            <h2>Admin Dashboard</h2>
            <nav>
                <a href="/admin/">Dashboard</a>
                <a href="/admin/costs">Cost Analysis</a>
                <a href="/admin/performance">Performance</a>
                <a href="/admin/prompts" class="active">Prompts</a>
                <a href="/admin/models">Models</a>
                <a href="/admin/pricing">Pricing</a>
                <a href="/admin/debug">Debug Tools</a>
            </nav>
        </div>
        <div class="content">
            <h1>Prompt Viewer</h1>
            <p class="subtitle">Browse all LLM API calls ({total:,} total)</p>

            <div class="filters">
                <select id="range" onchange="applyFilters()">
                    <option value="24h" {'selected' if range_param == '24h' else ''}>Last 24 hours</option>
                    <option value="7d" {'selected' if range_param == '7d' else ''}>Last 7 days</option>
                    <option value="30d" {'selected' if range_param == '30d' else ''}>Last 30 days</option>
                    <option value="all" {'selected' if range_param == 'all' else ''}>All time</option>
                </select>
                <select id="call_type" onchange="applyFilters()">
                    <option value="">All call types</option>
    '''

    for ct in call_types:
        html += f'<option value="{ct}" {"selected" if ct == selected_type else ""}>{ct}</option>'

    html += '''
                </select>
                <select id="provider" onchange="applyFilters()">
                    <option value="">All providers</option>
    '''

    for p in providers:
        html += f'<option value="{p}" {"selected" if p == selected_provider else ""}>{p}</option>'

    html += '''
                </select>
            </div>

            <div class="card">
                <table>
                    <thead>
                        <tr>
                            <th>Time</th>
                            <th>Provider</th>
                            <th>Model</th>
                            <th>Type</th>
                            <th>Player</th>
                            <th>In/Out Tokens</th>
                            <th>Latency</th>
                            <th>Cost</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody>
    '''

    for row in rows:
        status_class = 'error' if row['status'] == 'error' else ''
        html += f'''
                        <tr>
                            <td>{row['created_at'][:19]}</td>
                            <td>{row['provider'] or '-'}</td>
                            <td>{row['model'] or '-'}</td>
                            <td>{row['call_type'] or '-'}</td>
                            <td>{row['player_name'] or '-'}</td>
                            <td>{(row['input_tokens'] or 0):,} / {(row['output_tokens'] or 0):,}</td>
                            <td>{(row['latency_ms'] or 0):,}ms</td>
                            <td class="cost">${(row['estimated_cost'] or 0):.4f}</td>
                            <td class="{status_class}">{row['status']}</td>
                        </tr>
        '''

    html += f'''
                    </tbody>
                </table>
            </div>

            <div class="pagination">
    '''

    if page > 1:
        html += f'<a href="javascript:goPage({page - 1})">Previous</a>'
    html += f'<span class="current">Page {page} of {total_pages}</span>'
    if page < total_pages:
        html += f'<a href="javascript:goPage({page + 1})">Next</a>'

    html += f'''
            </div>
        </div>

        <script>
            function applyFilters() {{
                const range = document.getElementById('range').value;
                const callType = document.getElementById('call_type').value;
                const provider = document.getElementById('provider').value;
                let url = '/admin/prompts?range=' + range;
                if (callType) url += '&call_type=' + encodeURIComponent(callType);
                if (provider) url += '&provider=' + encodeURIComponent(provider);
                window.location.href = url;
            }}
            function goPage(page) {{
                const params = new URLSearchParams(window.location.search);
                params.set('page', page);
                window.location.href = '/admin/prompts?' + params.toString();
            }}
        </script>
    </body>
    </html>
    '''
    return html


# =============================================================================
# Models Manager
# =============================================================================

@admin_dashboard_bp.route('/models')
@_dev_only
def models():
    """Model manager page - enable/disable models for game UI."""
    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            # Check if table exists (migration may not have run)
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='enabled_models'
            """)
            if not cursor.fetchone():
                return _render_models_migration_needed()

            cursor = conn.execute("""
                SELECT id, provider, model, enabled, display_name, notes,
                       supports_reasoning, supports_json_mode, supports_image_gen
                FROM enabled_models
                ORDER BY provider, sort_order
            """)
            rows = [dict(row) for row in cursor.fetchall()]

        return _render_models(rows)

    except Exception as e:
        logger.error(f"Models error: {e}")
        return _render_dashboard_error(str(e))


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
    """Toggle a model's enabled status."""
    data = request.get_json()
    enabled = data.get('enabled', False)

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            cursor = conn.execute("""
                UPDATE enabled_models
                SET enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (1 if enabled else 0, model_id))

            if cursor.rowcount == 0:
                return jsonify({'success': False, 'error': 'Model not found'}), 404

            return jsonify({'success': True, 'enabled': enabled})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# Pricing Manager (Placeholder - UI for existing API)
# =============================================================================

@admin_dashboard_bp.route('/pricing')
@_dev_only
def pricing():
    """Pricing manager page - UI for existing pricing API."""
    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            cursor = conn.execute("""
                SELECT id, provider, model, unit, cost, valid_from, valid_until, notes
                FROM model_pricing
                WHERE valid_until IS NULL OR valid_until > datetime('now')
                ORDER BY provider, model, unit
            """)
            rows = [dict(row) for row in cursor.fetchall()]

        return _render_pricing(rows)

    except Exception as e:
        logger.error(f"Pricing error: {e}")
        return _render_dashboard_error(str(e))


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
# Debug Tools
# =============================================================================

@admin_dashboard_bp.route('/debug')
@_dev_only
def debug_page():
    """Debug page with links to debug endpoints."""
    active_games = game_state_service.list_game_ids()

    games_html = ''
    if active_games:
        for game_id in active_games:
            games_html += f'<span class="game-id">{game_id}</span> '
    else:
        games_html = '<em>No active games</em>'

    return f'''
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
