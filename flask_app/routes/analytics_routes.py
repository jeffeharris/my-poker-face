"""Analytics routes for LLM usage analysis and model management."""

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from flask import Blueprint, jsonify, request

from .. import config

logger = logging.getLogger(__name__)

analytics_bp = Blueprint('analytics', __name__, url_prefix='/analytics')


def _get_db_path() -> str:
    """Get the database path based on environment."""
    if Path('/app/data').exists():
        return '/app/data/poker_games.db'
    return str(Path(__file__).parent.parent.parent / 'poker_games.db')


def _get_date_filter(range_param: str) -> str:
    """Convert range parameter to SQL datetime filter."""
    if range_param == '24h':
        return "datetime('now', '-1 day')"
    elif range_param == '7d':
        return "datetime('now', '-7 days')"
    elif range_param == '30d':
        return "datetime('now', '-30 days')"
    else:  # 'all' or default
        return "datetime('1970-01-01')"


def _dev_only(f):
    """Decorator to restrict route to development mode only."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not config.is_development:
            return jsonify({'error': 'Analytics only available in development mode'}), 403
        return f(*args, **kwargs)
    return decorated


# =============================================================================
# Dashboard
# =============================================================================

@analytics_bp.route('/')
@_dev_only
def dashboard():
    """Main analytics dashboard."""
    range_param = request.args.get('range', '7d')
    date_filter = _get_date_filter(range_param)

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            # Summary metrics
            cursor = conn.execute(f"""
                SELECT
                    COUNT(*) as total_calls,
                    COALESCE(SUM(estimated_cost), 0) as total_cost,
                    COALESCE(AVG(latency_ms), 0) as avg_latency,
                    COALESCE(SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0), 0) as error_rate
                FROM api_usage
                WHERE created_at >= {date_filter}
            """)
            summary = dict(cursor.fetchone())

            # Cost by provider
            cursor = conn.execute(f"""
                SELECT
                    provider,
                    COUNT(*) as calls,
                    COALESCE(SUM(estimated_cost), 0) as cost
                FROM api_usage
                WHERE created_at >= {date_filter}
                GROUP BY provider
                ORDER BY cost DESC
            """)
            cost_by_provider = [dict(row) for row in cursor.fetchall()]

            # Calls by type
            cursor = conn.execute(f"""
                SELECT
                    call_type,
                    COUNT(*) as calls,
                    COALESCE(SUM(estimated_cost), 0) as cost
                FROM api_usage
                WHERE created_at >= {date_filter}
                GROUP BY call_type
                ORDER BY calls DESC
            """)
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
        <title>LLM Analytics Dashboard</title>
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
            <h2>LLM Analytics</h2>
            <nav>
                <a href="/analytics/" class="active">Dashboard</a>
                <a href="/analytics/costs">Cost Analysis</a>
                <a href="/analytics/performance">Performance</a>
                <a href="/analytics/prompts">Prompts</a>
                <a href="/analytics/models">Models</a>
                <a href="/analytics/pricing">Pricing</a>
            </nav>
        </div>

        <div class="content">
            <h1>LLM Analytics Dashboard</h1>
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
                window.location.href = '/analytics/?range=' + range;
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
        <title>LLM Analytics - Error</title>
        <style>
            body {{ font-family: sans-serif; background: #1a1a2e; color: #eee; padding: 40px; }}
            .error {{ background: #ef4444; padding: 20px; border-radius: 8px; }}
        </style>
    </head>
    <body>
        <h1>Analytics Error</h1>
        <div class="error">{error}</div>
        <p><a href="/analytics/" style="color: #4ecca3;">Try again</a></p>
    </body>
    </html>
    '''


# =============================================================================
# API Endpoints (for AJAX updates)
# =============================================================================

@analytics_bp.route('/api/summary')
@_dev_only
def api_summary():
    """JSON endpoint for dashboard summary data."""
    range_param = request.args.get('range', '7d')
    date_filter = _get_date_filter(range_param)

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            cursor = conn.execute(f"""
                SELECT
                    COUNT(*) as total_calls,
                    COALESCE(SUM(estimated_cost), 0) as total_cost,
                    COALESCE(AVG(latency_ms), 0) as avg_latency,
                    COALESCE(SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0), 0) as error_rate
                FROM api_usage
                WHERE created_at >= {date_filter}
            """)
            summary = dict(cursor.fetchone())

            return jsonify({'success': True, 'summary': summary})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# Cost Analysis
# =============================================================================

@analytics_bp.route('/costs')
@_dev_only
def costs():
    """Cost analysis page with detailed breakdowns."""
    range_param = request.args.get('range', '7d')
    date_filter = _get_date_filter(range_param)

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            # Cost by model
            cursor = conn.execute(f"""
                SELECT
                    provider,
                    model,
                    COUNT(*) as calls,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    COALESCE(SUM(estimated_cost), 0) as cost
                FROM api_usage
                WHERE created_at >= {date_filter}
                GROUP BY provider, model
                ORDER BY cost DESC
            """)
            by_model = [dict(row) for row in cursor.fetchall()]

            # Cost by call type
            cursor = conn.execute(f"""
                SELECT
                    call_type,
                    COUNT(*) as calls,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    COALESCE(SUM(estimated_cost), 0) as cost
                FROM api_usage
                WHERE created_at >= {date_filter}
                GROUP BY call_type
                ORDER BY cost DESC
            """)
            by_type = [dict(row) for row in cursor.fetchall()]

            # Daily time series
            cursor = conn.execute(f"""
                SELECT
                    DATE(created_at) as date,
                    provider,
                    COALESCE(SUM(estimated_cost), 0) as cost,
                    COUNT(*) as calls
                FROM api_usage
                WHERE created_at >= {date_filter}
                GROUP BY DATE(created_at), provider
                ORDER BY date
            """)
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
        <title>Cost Analysis - LLM Analytics</title>
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
            <h2>LLM Analytics</h2>
            <nav>
                <a href="/analytics/">Dashboard</a>
                <a href="/analytics/costs" class="active">Cost Analysis</a>
                <a href="/analytics/performance">Performance</a>
                <a href="/analytics/prompts">Prompts</a>
                <a href="/analytics/models">Models</a>
                <a href="/analytics/pricing">Pricing</a>
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
            function setRange(range) {{ window.location.href = '/analytics/costs?range=' + range; }}

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

@analytics_bp.route('/performance')
@_dev_only
def performance():
    """Performance metrics page with latency and error analysis."""
    range_param = request.args.get('range', '7d')
    date_filter = _get_date_filter(range_param)

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            # Latency by provider
            cursor = conn.execute(f"""
                SELECT provider, latency_ms
                FROM api_usage
                WHERE created_at >= {date_filter}
                  AND status = 'ok'
                  AND latency_ms IS NOT NULL
            """)
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

            # Error rates by provider/model
            cursor = conn.execute(f"""
                SELECT
                    provider,
                    model,
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
                    COALESCE(SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0), 0) as error_rate
                FROM api_usage
                WHERE created_at >= {date_filter}
                GROUP BY provider, model
                ORDER BY error_rate DESC
            """)
            error_rates = [dict(row) for row in cursor.fetchall()]

            # Token efficiency
            cursor = conn.execute(f"""
                SELECT
                    provider,
                    AVG(CAST(output_tokens AS FLOAT) / NULLIF(input_tokens, 0)) as output_ratio,
                    SUM(cached_tokens) * 100.0 / NULLIF(SUM(input_tokens), 0) as cache_rate,
                    COUNT(*) as calls
                FROM api_usage
                WHERE created_at >= {date_filter}
                  AND status = 'ok'
                  AND input_tokens > 0
                GROUP BY provider
            """)
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
        <title>Performance - LLM Analytics</title>
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
            <h2>LLM Analytics</h2>
            <nav>
                <a href="/analytics/">Dashboard</a>
                <a href="/analytics/costs">Cost Analysis</a>
                <a href="/analytics/performance" class="active">Performance</a>
                <a href="/analytics/prompts">Prompts</a>
                <a href="/analytics/models">Models</a>
                <a href="/analytics/pricing">Pricing</a>
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
            function setRange(range) { window.location.href = '/analytics/performance?range=' + range; }
        </script>
    </body>
    </html>
    '''
    return html


# =============================================================================
# Prompt Viewer
# =============================================================================

@analytics_bp.route('/prompts')
@_dev_only
def prompts():
    """Prompt viewer with filtering and pagination."""
    range_param = request.args.get('range', '7d')
    call_type = request.args.get('call_type', '')
    provider = request.args.get('provider', '')
    page = int(request.args.get('page', 1))
    per_page = 50

    date_filter = _get_date_filter(range_param)

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row

            # Build query with filters
            where_clauses = [f"created_at >= {date_filter}"]
            params = []

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
        <title>Prompts - LLM Analytics</title>
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
            <h2>LLM Analytics</h2>
            <nav>
                <a href="/analytics/">Dashboard</a>
                <a href="/analytics/costs">Cost Analysis</a>
                <a href="/analytics/performance">Performance</a>
                <a href="/analytics/prompts" class="active">Prompts</a>
                <a href="/analytics/models">Models</a>
                <a href="/analytics/pricing">Pricing</a>
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
                let url = '/analytics/prompts?range=' + range;
                if (callType) url += '&call_type=' + encodeURIComponent(callType);
                if (provider) url += '&provider=' + encodeURIComponent(provider);
                window.location.href = url;
            }}
            function goPage(page) {{
                const params = new URLSearchParams(window.location.search);
                params.set('page', page);
                window.location.href = '/analytics/prompts?' + params.toString();
            }}
        </script>
    </body>
    </html>
    '''
    return html


# =============================================================================
# Models Manager
# =============================================================================

@analytics_bp.route('/models')
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
        <title>Models - LLM Analytics</title>
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
            <h2>LLM Analytics</h2>
            <nav>
                <a href="/analytics/">Dashboard</a>
                <a href="/analytics/costs">Cost Analysis</a>
                <a href="/analytics/performance">Performance</a>
                <a href="/analytics/prompts">Prompts</a>
                <a href="/analytics/models" class="active">Models</a>
                <a href="/analytics/pricing">Pricing</a>
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
        <title>Models - LLM Analytics</title>
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
            <h2>LLM Analytics</h2>
            <nav>
                <a href="/analytics/">Dashboard</a>
                <a href="/analytics/costs">Cost Analysis</a>
                <a href="/analytics/performance">Performance</a>
                <a href="/analytics/prompts">Prompts</a>
                <a href="/analytics/models" class="active">Models</a>
                <a href="/analytics/pricing">Pricing</a>
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
                    const resp = await fetch('/analytics/api/models/' + id + '/toggle', {
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


@analytics_bp.route('/api/models/<int:model_id>/toggle', methods=['POST'])
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

@analytics_bp.route('/pricing')
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
        <title>Pricing - LLM Analytics</title>
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
            <h2>LLM Analytics</h2>
            <nav>
                <a href="/analytics/">Dashboard</a>
                <a href="/analytics/costs">Cost Analysis</a>
                <a href="/analytics/performance">Performance</a>
                <a href="/analytics/prompts">Prompts</a>
                <a href="/analytics/models">Models</a>
                <a href="/analytics/pricing" class="active">Pricing</a>
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
