import { useState, useEffect, useCallback } from 'react';
import { config } from '../../config';
import './PricingManager.css';

// ============================================
// Types
// ============================================

interface PricingEntry {
  id: number;
  provider: string;
  model: string;
  unit: string;
  cost: number;
  valid_from: string | null;
  valid_until: string | null;
  notes: string | null;
}

interface AlertState {
  type: 'success' | 'error' | 'info';
  message: string;
}

interface PricingManagerProps {
  embedded?: boolean;
}

interface NewPricing {
  provider: string;
  model: string;
  unit: string;
  cost: string;
  notes: string;
}

// ============================================
// Main Component
// ============================================

export function PricingManager({ embedded = false }: PricingManagerProps) {
  const [entries, setEntries] = useState<PricingEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [alert, setAlert] = useState<AlertState | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [filterProvider, setFilterProvider] = useState('');
  const [currentOnly, setCurrentOnly] = useState(true);
  const [providers, setProviders] = useState<string[]>([]);
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null);
  const [newPricing, setNewPricing] = useState<NewPricing>({
    provider: '',
    model: '',
    unit: 'input_tokens_1m',
    cost: '',
    notes: '',
  });

  // Fetch pricing entries
  const fetchPricing = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams();
      if (filterProvider) params.append('provider', filterProvider);
      if (currentOnly) params.append('current_only', 'true');

      const response = await fetch(`${config.API_URL}/admin/pricing?${params}`);
      const data = await response.json();

      if (data.success) {
        setEntries(data.pricing || []);
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to load pricing' });
      }
    } catch (error) {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    } finally {
      setLoading(false);
    }
  }, [filterProvider, currentOnly]);

  // Fetch providers
  const fetchProviders = useCallback(async () => {
    try {
      const response = await fetch(`${config.API_URL}/admin/pricing/providers`);
      const data = await response.json();
      if (data.success) {
        setProviders(data.providers.map((p: { provider: string }) => p.provider));
      }
    } catch (error) {
      console.error('Failed to fetch providers:', error);
    }
  }, []);

  useEffect(() => {
    fetchPricing();
    fetchProviders();
  }, [fetchPricing, fetchProviders]);

  // Add new pricing entry
  const handleAddPricing = async () => {
    if (!newPricing.provider || !newPricing.model || !newPricing.unit || !newPricing.cost) {
      setAlert({ type: 'error', message: 'Please fill in all required fields' });
      return;
    }

    try {
      const response = await fetch(`${config.API_URL}/admin/pricing`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: newPricing.provider,
          model: newPricing.model,
          unit: newPricing.unit,
          cost: parseFloat(newPricing.cost),
          notes: newPricing.notes || undefined,
        }),
      });

      const data = await response.json();

      if (data.success) {
        setAlert({ type: 'success', message: 'Pricing entry added' });
        setShowAddModal(false);
        setNewPricing({ provider: '', model: '', unit: 'input_tokens_1m', cost: '', notes: '' });
        fetchPricing();
        fetchProviders();
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to add pricing' });
      }
    } catch (error) {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    }
  };

  // Delete pricing entry
  const handleDelete = async (id: number) => {
    try {
      const response = await fetch(`${config.API_URL}/admin/pricing/${id}`, {
        method: 'DELETE',
      });

      const data = await response.json();

      if (data.success) {
        setAlert({ type: 'success', message: 'Pricing entry deleted' });
        setDeleteConfirm(null);
        fetchPricing();
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to delete pricing' });
      }
    } catch (error) {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    }
  };

  // Format cost for display
  const formatCost = (cost: number, unit: string) => {
    const isPerMillion = unit.includes('_1m');
    if (isPerMillion) {
      return `$${cost.toFixed(2)} / 1M`;
    }
    return `$${cost.toFixed(4)}`;
  };

  // Clear alert after timeout
  useEffect(() => {
    if (alert) {
      const timer = setTimeout(() => setAlert(null), 3000);
      return () => clearTimeout(timer);
    }
  }, [alert]);

  if (loading && entries.length === 0) {
    return (
      <div className="prm-loading">
        <div className="prm-loading__spinner" />
        <span>Loading pricing...</span>
      </div>
    );
  }

  return (
    <div className={`prm-container ${embedded ? 'prm-container--embedded' : ''}`}>
      {/* Alert Toast */}
      {alert && (
        <div className={`prm-alert prm-alert--${alert.type}`}>
          <span className="prm-alert__icon">
            {alert.type === 'success' ? '✓' : alert.type === 'error' ? '✕' : 'i'}
          </span>
          <span className="prm-alert__message">{alert.message}</span>
          <button className="prm-alert__close" onClick={() => setAlert(null)}>×</button>
        </div>
      )}

      {/* Header */}
      <div className="prm-header">
        <div className="prm-header__title-row">
          <h2 className="prm-header__title">Pricing Configuration</h2>
          <button className="prm-btn prm-btn--primary" onClick={() => setShowAddModal(true)}>
            + Add Entry
          </button>
        </div>
        <p className="prm-header__subtitle">Manage LLM model pricing for cost calculations</p>
      </div>

      {/* Filters */}
      <div className="prm-filters">
        <select
          className="prm-select"
          value={filterProvider}
          onChange={(e) => setFilterProvider(e.target.value)}
        >
          <option value="">All Providers</option>
          {providers.map(p => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>

        <label className="prm-checkbox">
          <input
            type="checkbox"
            checked={currentOnly}
            onChange={(e) => setCurrentOnly(e.target.checked)}
          />
          <span>Current only</span>
        </label>
      </div>

      {/* Pricing Table */}
      <div className="prm-table-wrapper">
        <table className="prm-table">
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
          <tbody>
            {entries.length === 0 ? (
              <tr>
                <td colSpan={6} className="prm-table__empty">
                  No pricing entries found
                </td>
              </tr>
            ) : (
              entries.map(entry => (
                <tr key={entry.id} className={entry.valid_until ? 'prm-table__row--expired' : ''}>
                  <td>{entry.provider}</td>
                  <td className="prm-table__model">{entry.model}</td>
                  <td>{entry.unit}</td>
                  <td className="prm-table__cost">{formatCost(entry.cost, entry.unit)}</td>
                  <td>{entry.valid_from ? new Date(entry.valid_from).toLocaleDateString() : '-'}</td>
                  <td>
                    {deleteConfirm === entry.id ? (
                      <div className="prm-confirm">
                        <button
                          className="prm-btn prm-btn--danger prm-btn--sm"
                          onClick={() => handleDelete(entry.id)}
                        >
                          Confirm
                        </button>
                        <button
                          className="prm-btn prm-btn--ghost prm-btn--sm"
                          onClick={() => setDeleteConfirm(null)}
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        className="prm-btn prm-btn--ghost prm-btn--sm"
                        onClick={() => setDeleteConfirm(entry.id)}
                      >
                        Delete
                      </button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Add Modal */}
      {showAddModal && (
        <div className="prm-modal-overlay" onClick={() => setShowAddModal(false)}>
          <div className="prm-modal" onClick={(e) => e.stopPropagation()}>
            <h3 className="prm-modal__title">Add Pricing Entry</h3>

            <div className="prm-form">
              <div className="prm-form__group">
                <label>Provider *</label>
                <input
                  type="text"
                  className="prm-input"
                  value={newPricing.provider}
                  onChange={(e) => setNewPricing(p => ({ ...p, provider: e.target.value }))}
                  placeholder="e.g., openai"
                  list="providers-list"
                />
                <datalist id="providers-list">
                  {providers.map(p => (
                    <option key={p} value={p} />
                  ))}
                </datalist>
              </div>

              <div className="prm-form__group">
                <label>Model *</label>
                <input
                  type="text"
                  className="prm-input"
                  value={newPricing.model}
                  onChange={(e) => setNewPricing(p => ({ ...p, model: e.target.value }))}
                  placeholder="e.g., gpt-4o"
                />
              </div>

              <div className="prm-form__group">
                <label>Unit *</label>
                <select
                  className="prm-select"
                  value={newPricing.unit}
                  onChange={(e) => setNewPricing(p => ({ ...p, unit: e.target.value }))}
                >
                  <option value="input_tokens_1m">Input Tokens (per 1M)</option>
                  <option value="output_tokens_1m">Output Tokens (per 1M)</option>
                  <option value="cached_input_tokens_1m">Cached Input Tokens (per 1M)</option>
                  <option value="reasoning_tokens_1m">Reasoning Tokens (per 1M)</option>
                  <option value="image_1024x1024">Image (1024x1024)</option>
                  <option value="image_512x512">Image (512x512)</option>
                </select>
              </div>

              <div className="prm-form__group">
                <label>Cost (USD) *</label>
                <input
                  type="number"
                  className="prm-input"
                  value={newPricing.cost}
                  onChange={(e) => setNewPricing(p => ({ ...p, cost: e.target.value }))}
                  placeholder="e.g., 2.50"
                  step="0.001"
                  min="0"
                />
              </div>

              <div className="prm-form__group">
                <label>Notes</label>
                <input
                  type="text"
                  className="prm-input"
                  value={newPricing.notes}
                  onChange={(e) => setNewPricing(p => ({ ...p, notes: e.target.value }))}
                  placeholder="Optional notes"
                />
              </div>
            </div>

            <div className="prm-modal__actions">
              <button className="prm-btn prm-btn--ghost" onClick={() => setShowAddModal(false)}>
                Cancel
              </button>
              <button className="prm-btn prm-btn--primary" onClick={handleAddPricing}>
                Add Entry
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default PricingManager;
