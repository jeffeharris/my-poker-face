import { useState, useEffect, useCallback } from 'react';
import { adminAPI } from '../../utils/api';
import './CaptureSettings.css';

// ============================================
// Types
// ============================================

interface SettingConfig {
  value: string;
  options?: string[];
  type?: string;
  description: string;
  env_default: string;
  is_db_override: boolean;
}

interface Settings {
  LLM_PROMPT_CAPTURE: SettingConfig;
  LLM_PROMPT_RETENTION_DAYS: SettingConfig;
}

interface CaptureStats {
  total: number;
  by_call_type?: Record<string, number>;
  by_provider?: Record<string, number>;
}

interface CategoryStats {
  rows: number;
  bytes: number;
  percentage: number;
}

interface StorageStats {
  total_bytes: number;
  total_mb: number;
  categories: Record<string, CategoryStats>;
  tables: Record<string, { rows: number; bytes: number }>;
}

interface AlertState {
  type: 'success' | 'error' | 'info';
  message: string;
}

interface CaptureSettingsProps {
  embedded?: boolean;
}

// ============================================
// Main Component
// ============================================

export function CaptureSettings({ embedded = false }: CaptureSettingsProps) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [stats, setStats] = useState<CaptureStats | null>(null);
  const [storage, setStorage] = useState<StorageStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [alert, setAlert] = useState<AlertState | null>(null);

  // Track edited values separately from saved values
  const [editedCapture, setEditedCapture] = useState<string>('');
  const [editedRetention, setEditedRetention] = useState<string>('');

  // Fetch settings and stats
  const fetchData = useCallback(async () => {
    try {
      setLoading(true);

      // Fetch settings, stats, and storage in parallel
      const [settingsRes, statsRes, storageRes] = await Promise.all([
        adminAPI.fetch('/admin/api/settings'),
        adminAPI.fetch('/admin/api/playground/stats'),
        adminAPI.fetch('/admin/api/settings/storage'),
      ]);

      const settingsData = await settingsRes.json();
      const statsData = await statsRes.json();
      const storageData = await storageRes.json();

      if (settingsData.success) {
        setSettings(settingsData.settings);
        setEditedCapture(settingsData.settings.LLM_PROMPT_CAPTURE.value);
        setEditedRetention(settingsData.settings.LLM_PROMPT_RETENTION_DAYS.value);
      } else {
        setAlert({ type: 'error', message: settingsData.error || 'Failed to load settings' });
      }

      if (statsData.success) {
        setStats(statsData.stats);
      }

      if (storageData.success) {
        setStorage(storageData.storage);
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Save a setting
  const saveSetting = async (key: string, value: string) => {
    try {
      setSaving(true);
      const response = await adminAPI.fetch('/admin/api/settings', {
        method: 'POST',
        body: JSON.stringify({ key, value }),
      });

      const data = await response.json();

      if (data.success) {
        setAlert({ type: 'success', message: data.message });
        // Update local state to reflect the change
        if (settings) {
          setSettings({
            ...settings,
            [key]: {
              ...settings[key as keyof Settings],
              value,
              is_db_override: true,
            },
          });
        }
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to save setting' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    } finally {
      setSaving(false);
    }
  };

  // Reset settings to defaults
  const resetSettings = async () => {
    try {
      setSaving(true);
      const response = await adminAPI.fetch('/admin/api/settings/reset', {
        method: 'POST',
      });

      const data = await response.json();

      if (data.success) {
        setAlert({ type: 'success', message: data.message });
        // Refresh settings to show defaults
        await fetchData();
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to reset settings' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    } finally {
      setSaving(false);
    }
  };

  // Check if there are unsaved changes
  const hasChanges = settings && (
    editedCapture !== settings.LLM_PROMPT_CAPTURE.value ||
    editedRetention !== settings.LLM_PROMPT_RETENTION_DAYS.value
  );

  // Save all changes
  const saveAllChanges = async () => {
    if (!settings) return;

    if (editedCapture !== settings.LLM_PROMPT_CAPTURE.value) {
      await saveSetting('LLM_PROMPT_CAPTURE', editedCapture);
    }
    if (editedRetention !== settings.LLM_PROMPT_RETENTION_DAYS.value) {
      await saveSetting('LLM_PROMPT_RETENTION_DAYS', editedRetention);
    }
  };

  // Clear alert after timeout
  useEffect(() => {
    if (alert) {
      const timer = setTimeout(() => setAlert(null), 3000);
      return () => clearTimeout(timer);
    }
  }, [alert]);

  if (loading) {
    return (
      <div className="cs-loading">
        <div className="cs-loading__spinner" />
        <span>Loading settings...</span>
      </div>
    );
  }

  if (!settings) {
    return (
      <div className="cs-error">
        Failed to load settings. Please try again.
      </div>
    );
  }

  return (
    <div className={`cs-container ${embedded ? 'cs-container--embedded' : ''}`}>
      {/* Alert Toast */}
      {alert && (
        <div className={`cs-alert cs-alert--${alert.type}`}>
          <span className="cs-alert__icon">
            {alert.type === 'success' ? '✓' : alert.type === 'error' ? '✕' : 'i'}
          </span>
          <span className="cs-alert__message">{alert.message}</span>
          <button className="cs-alert__close" onClick={() => setAlert(null)}>×</button>
        </div>
      )}

      {/* Header */}
      <div className="cs-header">
        <h2 className="cs-header__title">Capture Settings</h2>
        <p className="cs-header__subtitle">Configure LLM prompt capture for debugging and replay</p>
      </div>

      {/* Settings Card */}
      <div className="cs-card">
        <h3 className="cs-card__title">Prompt Capture</h3>

        {/* Capture Mode */}
        <div className="cs-setting">
          <div className="cs-setting__header">
            <label className="cs-setting__label">Capture Mode</label>
            {settings.LLM_PROMPT_CAPTURE.is_db_override && (
              <span className="cs-setting__badge">Custom</span>
            )}
          </div>
          <p className="cs-setting__description">
            {settings.LLM_PROMPT_CAPTURE.description}
          </p>
          <div className="cs-setting__options">
            {settings.LLM_PROMPT_CAPTURE.options?.map(option => (
              <button
                key={option}
                className={`cs-option ${editedCapture === option ? 'cs-option--selected' : ''}`}
                onClick={() => setEditedCapture(option)}
                disabled={saving}
                type="button"
              >
                <span className="cs-option__label">{formatOptionLabel(option)}</span>
                {option === settings.LLM_PROMPT_CAPTURE.env_default && (
                  <span className="cs-option__default">default</span>
                )}
              </button>
            ))}
          </div>
        </div>

        {/* Retention Days */}
        <div className="cs-setting">
          <div className="cs-setting__header">
            <label className="cs-setting__label">Retention Period</label>
            {settings.LLM_PROMPT_RETENTION_DAYS.is_db_override && (
              <span className="cs-setting__badge">Custom</span>
            )}
          </div>
          <p className="cs-setting__description">
            {settings.LLM_PROMPT_RETENTION_DAYS.description}
          </p>
          <div className="cs-setting__input-row">
            <input
              type="number"
              className="cs-input"
              value={editedRetention}
              onChange={(e) => setEditedRetention(e.target.value)}
              min="0"
              disabled={saving}
            />
            <span className="cs-input__suffix">days</span>
            {editedRetention === '0' && (
              <span className="cs-input__hint">Unlimited retention</span>
            )}
          </div>
        </div>

        {/* Action Buttons */}
        <div className="cs-actions">
          <button
            className="cs-btn cs-btn--primary"
            onClick={saveAllChanges}
            disabled={saving || !hasChanges}
            type="button"
          >
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
          <button
            className="cs-btn cs-btn--secondary"
            onClick={resetSettings}
            disabled={saving}
            type="button"
          >
            Reset to Defaults
          </button>
        </div>
      </div>

      {/* Statistics Card */}
      {stats && (
        <div className="cs-card">
          <h3 className="cs-card__title">Capture Statistics</h3>
          <div className="cs-stats">
            <div className="cs-stat">
              <span className="cs-stat__value">{stats.total?.toLocaleString() || 0}</span>
              <span className="cs-stat__label">Total Captures</span>
            </div>
            {stats.by_call_type && Object.keys(stats.by_call_type).length > 0 && (
              <div className="cs-stat-breakdown">
                <span className="cs-stat-breakdown__title">By Call Type</span>
                <div className="cs-stat-breakdown__items">
                  {Object.entries(stats.by_call_type)
                    .sort(([, a], [, b]) => b - a)
                    .slice(0, 5)
                    .map(([type, count]) => (
                      <div key={type} className="cs-stat-item">
                        <span className="cs-stat-item__label">{type}</span>
                        <span className="cs-stat-item__value">{count.toLocaleString()}</span>
                      </div>
                    ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Storage Card */}
      {storage && (
        <div className="cs-card">
          <h3 className="cs-card__title">Database Storage</h3>
          <div className="cs-storage">
            <div className="cs-storage__total">
              <span className="cs-storage__total-value">{storage.total_mb.toFixed(2)}</span>
              <span className="cs-storage__total-unit">MB</span>
              <span className="cs-storage__total-label">Total Database Size</span>
            </div>
            <div className="cs-storage__breakdown">
              {Object.entries(storage.categories)
                .sort(([, a], [, b]) => b.bytes - a.bytes)
                .map(([category, stats]) => (
                  <div key={category} className="cs-storage__category">
                    <div className="cs-storage__category-header">
                      <span className="cs-storage__category-name">{formatCategoryName(category)}</span>
                      <span className="cs-storage__category-size">{formatBytes(stats.bytes)}</span>
                    </div>
                    <div className="cs-storage__bar">
                      <div
                        className={`cs-storage__bar-fill cs-storage__bar-fill--${category}`}
                        style={{ width: `${Math.max(stats.percentage, 1)}%` }}
                      />
                    </div>
                    <div className="cs-storage__category-meta">
                      <span>{stats.rows.toLocaleString()} rows</span>
                      <span>{stats.percentage.toFixed(1)}%</span>
                    </div>
                  </div>
                ))}
            </div>
          </div>
        </div>
      )}

      {/* Info Card */}
      <div className="cs-card cs-card--info">
        <h3 className="cs-card__title">How It Works</h3>
        <ul className="cs-info-list">
          <li>Changes take effect immediately - no server restart needed</li>
          <li>Settings are stored in the database and override environment variables</li>
          <li>"Reset to Defaults" removes database overrides, reverting to env vars</li>
          <li>Captured prompts can be viewed and replayed in the Prompt Playground</li>
        </ul>
      </div>
    </div>
  );
}

// Helper to format option labels
function formatOptionLabel(option: string): string {
  switch (option) {
    case 'disabled':
      return 'Disabled';
    case 'all':
      return 'Capture All';
    case 'all_except_decisions':
      return 'All Except Decisions';
    default:
      return option;
  }
}

// Helper to format category names
function formatCategoryName(category: string): string {
  const names: Record<string, string> = {
    captures: 'Prompt Captures',
    api_usage: 'API Usage Logs',
    game_data: 'Game Data',
    ai_state: 'AI State',
    config: 'Configuration',
    assets: 'Avatar Images',
    other: 'Other',
  };
  return names[category] || category;
}

// Helper to format bytes
function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(i > 1 ? 2 : 0)} ${sizes[i]}`;
}

export default CaptureSettings;
