import { useState, useEffect, useCallback } from 'react';
import { adminAPI } from '../../utils/api';
import { Plus, Edit2, Trash2, Save, X, ChevronDown, ChevronUp } from 'lucide-react';
import './AdminShared.css';
import './PromptPresetManager.css';

// ============================================
// Types
// ============================================

interface PromptConfig {
  [key: string]: boolean;
}

interface PromptPreset {
  id: number;
  name: string;
  description: string | null;
  prompt_config: PromptConfig | null;
  guidance_injection: string | null;
  owner_id: string | null;
  is_system: boolean;
  created_at: string;
  updated_at: string;
}

interface AlertState {
  type: 'success' | 'error' | 'info';
  message: string;
}

interface PromptPresetManagerProps {
  embedded?: boolean;
}

interface EditingPreset {
  id: number | null; // null = creating new
  name: string;
  description: string;
  prompt_config: PromptConfig;
  guidance_injection: string;
}

// ============================================
// Default Prompt Config Options
// ============================================

const DEFAULT_PROMPT_CONFIG_OPTIONS: Array<{ key: string; label: string; description: string }> = [
  { key: 'include_psychology', label: 'Psychology State', description: 'Include emotional state and tilt information' },
  { key: 'include_pot_odds', label: 'Pot Odds', description: 'Include pot odds calculations' },
  { key: 'include_position', label: 'Position Info', description: 'Include position-based strategy hints' },
  { key: 'include_stack_context', label: 'Stack Context', description: 'Include stack-to-pot ratio analysis' },
  { key: 'include_opponent_notes', label: 'Opponent Notes', description: 'Include learned opponent patterns' },
  { key: 'include_hand_history', label: 'Hand History', description: 'Include recent hand summary' },
  { key: 'verbose_reasoning', label: 'Verbose Reasoning', description: 'Request detailed decision explanation' },
];

const DEFAULT_PROMPT_CONFIG: PromptConfig = Object.fromEntries(
  DEFAULT_PROMPT_CONFIG_OPTIONS.map(opt => [opt.key, true])
);

// ============================================
// Main Component
// ============================================

export function PromptPresetManager({ embedded = false }: PromptPresetManagerProps) {
  const [presets, setPresets] = useState<PromptPreset[]>([]);
  const [loading, setLoading] = useState(true);
  const [alert, setAlert] = useState<AlertState | null>(null);
  const [editingPreset, setEditingPreset] = useState<EditingPreset | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null);

  // Fetch presets
  const fetchPresets = useCallback(async () => {
    try {
      setLoading(true);
      const response = await adminAPI.fetch('/api/prompt-presets');
      const data = await response.json();

      if (data.success) {
        setPresets(data.presets);
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to load presets' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPresets();
  }, [fetchPresets]);

  // Clear alert after timeout
  useEffect(() => {
    if (alert) {
      const timer = setTimeout(() => setAlert(null), 4000);
      return () => clearTimeout(timer);
    }
  }, [alert]);

  // Start creating new preset
  const startCreate = () => {
    setEditingPreset({
      id: null,
      name: '',
      description: '',
      prompt_config: { ...DEFAULT_PROMPT_CONFIG },
      guidance_injection: '',
    });
    setExpandedId(null);
  };

  // Start editing existing preset
  const startEdit = (preset: PromptPreset) => {
    setEditingPreset({
      id: preset.id,
      name: preset.name,
      description: preset.description || '',
      prompt_config: preset.prompt_config || { ...DEFAULT_PROMPT_CONFIG },
      guidance_injection: preset.guidance_injection || '',
    });
  };

  // Cancel editing
  const cancelEdit = () => {
    setEditingPreset(null);
  };

  // Save preset (create or update)
  const savePreset = async () => {
    if (!editingPreset) return;

    if (!editingPreset.name.trim()) {
      setAlert({ type: 'error', message: 'Name is required' });
      return;
    }

    try {
      const payload = {
        name: editingPreset.name.trim(),
        description: editingPreset.description.trim() || null,
        prompt_config: editingPreset.prompt_config,
        guidance_injection: editingPreset.guidance_injection.trim() || null,
      };

      const isNew = editingPreset.id === null;
      const url = isNew
        ? '/api/prompt-presets'
        : `/api/prompt-presets/${editingPreset.id}`;

      const response = await adminAPI.fetch(url, {
        method: isNew ? 'POST' : 'PUT',
        body: JSON.stringify(payload),
      });

      const data = await response.json();

      if (data.success) {
        setAlert({ type: 'success', message: data.message || `Preset ${isNew ? 'created' : 'updated'}` });
        setEditingPreset(null);
        fetchPresets();
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to save preset' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    }
  };

  // Delete preset
  const deletePreset = async (id: number) => {
    try {
      const response = await adminAPI.fetch(`/api/prompt-presets/${id}`, {
        method: 'DELETE',
      });

      const data = await response.json();

      if (data.success) {
        setAlert({ type: 'success', message: data.message || 'Preset deleted' });
        setDeleteConfirmId(null);
        setPresets(prev => prev.filter(p => p.id !== id));
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to delete preset' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    }
  };

  // Toggle prompt config option in editor
  const toggleConfigOption = (key: string) => {
    if (!editingPreset) return;
    setEditingPreset({
      ...editingPreset,
      prompt_config: {
        ...editingPreset.prompt_config,
        [key]: !editingPreset.prompt_config[key],
      },
    });
  };

  if (loading) {
    return (
      <div className="ppm-loading">
        <div className="ppm-loading__spinner" />
        <span>Loading presets...</span>
      </div>
    );
  }

  return (
    <div className={`ppm-container ${embedded ? 'ppm-container--embedded' : ''}`}>
      {/* Alert Toast */}
      {alert && (
        <div className={`ppm-alert ppm-alert--${alert.type}`}>
          <span className="ppm-alert__icon">
            {alert.type === 'success' ? '✓' : alert.type === 'error' ? '✕' : 'i'}
          </span>
          <span className="ppm-alert__message">{alert.message}</span>
          <button className="ppm-alert__close" onClick={() => setAlert(null)}>×</button>
        </div>
      )}

      {/* Header */}
      <div className="ppm-header">
        <div className="ppm-header__text">
          <h2 className="ppm-header__title">Prompt Presets</h2>
          <p className="ppm-header__subtitle">
            Saved prompt configurations for experiments
          </p>
        </div>
        <button
          className="ppm-header__add"
          onClick={startCreate}
          disabled={editingPreset !== null}
        >
          <Plus size={18} />
          New Preset
        </button>
      </div>

      {/* Create/Edit Form */}
      {editingPreset && (
        <div className="ppm-editor">
          <div className="ppm-editor__header">
            <h3>{editingPreset.id === null ? 'Create New Preset' : 'Edit Preset'}</h3>
          </div>

          <div className="ppm-editor__form">
            <div className="ppm-field">
              <label htmlFor="preset-name">Name *</label>
              <input
                id="preset-name"
                type="text"
                value={editingPreset.name}
                onChange={(e) => setEditingPreset({ ...editingPreset, name: e.target.value })}
                placeholder="e.g., aggressive_play"
              />
            </div>

            <div className="ppm-field">
              <label htmlFor="preset-description">Description</label>
              <input
                id="preset-description"
                type="text"
                value={editingPreset.description}
                onChange={(e) => setEditingPreset({ ...editingPreset, description: e.target.value })}
                placeholder="e.g., Configuration for testing aggressive strategies"
              />
            </div>

            <div className="ppm-field">
              <label>Prompt Configuration</label>
              <div className="ppm-config-options">
                {DEFAULT_PROMPT_CONFIG_OPTIONS.map(opt => (
                  <label key={opt.key} className="ppm-config-option">
                    <input
                      type="checkbox"
                      checked={editingPreset.prompt_config[opt.key] ?? true}
                      onChange={() => toggleConfigOption(opt.key)}
                    />
                    <span className="ppm-config-option__label">{opt.label}</span>
                    <span className="ppm-config-option__desc">{opt.description}</span>
                  </label>
                ))}
              </div>
            </div>

            <div className="ppm-field">
              <label htmlFor="preset-guidance">Guidance Injection</label>
              <textarea
                id="preset-guidance"
                value={editingPreset.guidance_injection}
                onChange={(e) => setEditingPreset({ ...editingPreset, guidance_injection: e.target.value })}
                placeholder="e.g., Always consider folding weak hands in early position..."
                rows={4}
              />
              <span className="ppm-field__hint">
                Text appended to the user message for extra instructions
              </span>
            </div>
          </div>

          <div className="ppm-editor__actions">
            <button className="ppm-btn ppm-btn--secondary" onClick={cancelEdit}>
              <X size={16} />
              Cancel
            </button>
            <button className="ppm-btn ppm-btn--primary" onClick={savePreset}>
              <Save size={16} />
              Save Preset
            </button>
          </div>
        </div>
      )}

      {/* Preset List */}
      <div className="ppm-list">
        {presets.length === 0 ? (
          <div className="ppm-empty">
            <p>No presets yet. Create one to get started.</p>
          </div>
        ) : (
          presets.map(preset => (
            <div
              key={preset.id}
              className={`ppm-preset ${expandedId === preset.id ? 'ppm-preset--expanded' : ''}`}
            >
              <div
                className="ppm-preset__header"
                onClick={() => setExpandedId(expandedId === preset.id ? null : preset.id)}
              >
                <div className="ppm-preset__info">
                  <span className="ppm-preset__name">{preset.name}</span>
                  {preset.description && (
                    <span className="ppm-preset__description">{preset.description}</span>
                  )}
                </div>
                <div className="ppm-preset__actions">
                  {preset.is_system && (
                    <span className="ppm-tag ppm-tag--system" title="Managed by config/game_modes.yaml">System</span>
                  )}
                  {!preset.is_system && (
                    <>
                      <button
                        className="ppm-preset__action"
                        onClick={(e) => { e.stopPropagation(); startEdit(preset); }}
                        title="Edit"
                        disabled={editingPreset !== null}
                      >
                        <Edit2 size={16} />
                      </button>
                      <button
                        className="ppm-preset__action ppm-preset__action--danger"
                        onClick={(e) => { e.stopPropagation(); setDeleteConfirmId(preset.id); }}
                        title="Delete"
                        disabled={editingPreset !== null}
                      >
                        <Trash2 size={16} />
                      </button>
                    </>
                  )}
                  <span className="ppm-preset__chevron">
                    {expandedId === preset.id ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
                  </span>
                </div>
              </div>

              {expandedId === preset.id && (
                <div className="ppm-preset__details">
                  <div className="ppm-preset__section">
                    <h4>Prompt Config</h4>
                    <div className="ppm-preset__config-tags">
                      {preset.prompt_config ? (
                        Object.entries(preset.prompt_config)
                          .filter(([, enabled]) => enabled)
                          .map(([key]) => {
                            const opt = DEFAULT_PROMPT_CONFIG_OPTIONS.find(o => o.key === key);
                            return (
                              <span key={key} className="ppm-tag ppm-tag--enabled">
                                {opt?.label || key}
                              </span>
                            );
                          })
                      ) : (
                        <span className="ppm-tag ppm-tag--default">Default (all enabled)</span>
                      )}
                    </div>
                  </div>
                  {preset.guidance_injection && (
                    <div className="ppm-preset__section">
                      <h4>Guidance Injection</h4>
                      <p className="ppm-preset__guidance">{preset.guidance_injection}</p>
                    </div>
                  )}
                  <div className="ppm-preset__meta">
                    <span>Created: {new Date(preset.created_at).toLocaleDateString()}</span>
                    <span>Updated: {new Date(preset.updated_at).toLocaleDateString()}</span>
                  </div>
                </div>
              )}

              {/* Delete Confirmation */}
              {deleteConfirmId === preset.id && (
                <div className="ppm-preset__delete-confirm">
                  <span>Delete "{preset.name}"?</span>
                  <button
                    className="ppm-btn ppm-btn--danger ppm-btn--small"
                    onClick={() => deletePreset(preset.id)}
                  >
                    Delete
                  </button>
                  <button
                    className="ppm-btn ppm-btn--secondary ppm-btn--small"
                    onClick={() => setDeleteConfirmId(null)}
                  >
                    Cancel
                  </button>
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export default PromptPresetManager;
