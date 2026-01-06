"""Admin routes for system configuration like pricing management."""

import sqlite3
from datetime import datetime
from flask import Blueprint, jsonify, request
from pathlib import Path

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def _get_db_path() -> str:
    """Get the database path based on environment."""
    if Path('/app/data').exists():
        return '/app/data/poker_games.db'
    return str(Path(__file__).parent.parent.parent / 'poker_games.db')


# =============================================================================
# Pricing Management
# =============================================================================

@admin_bp.route('/pricing', methods=['GET'])
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


@admin_bp.route('/pricing', methods=['POST'])
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
    cost = float(data['cost'])
    valid_from = data.get('valid_from') or datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
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

            return jsonify({
                'success': True,
                'message': f'Added pricing for {provider}/{model}/{unit}: ${cost}'
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/pricing/bulk', methods=['POST'])
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

    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    added = 0
    errors = []

    try:
        with sqlite3.connect(_get_db_path()) as conn:
            for entry in entries:
                try:
                    provider = entry['provider']
                    model = entry['model']
                    unit = entry['unit']
                    cost = float(entry['cost'])
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

            return jsonify({'success': True, 'added': added, 'errors': errors})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/pricing/<int:pricing_id>', methods=['DELETE'])
def delete_pricing(pricing_id: int):
    """Delete a pricing entry by ID."""
    try:
        with sqlite3.connect(_get_db_path()) as conn:
            cursor = conn.execute("DELETE FROM model_pricing WHERE id = ?", (pricing_id,))
            if cursor.rowcount == 0:
                return jsonify({'success': False, 'error': 'Not found'}), 404
            return jsonify({'success': True, 'message': f'Deleted pricing entry {pricing_id}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/pricing/providers', methods=['GET'])
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


@admin_bp.route('/pricing/models/<provider>', methods=['GET'])
def list_models(provider: str):
    """List all models for a provider."""
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
