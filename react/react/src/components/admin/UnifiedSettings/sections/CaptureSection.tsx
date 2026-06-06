import { useState, useEffect, useCallback } from 'react';
import { adminFetch } from '../../../../utils/api';
import type { CaptureSettingsData, CaptureStats, ShowAlert } from '../types';
import './CaptureSection.css';

interface CaptureSectionProps {
  showAlert: ShowAlert;
}

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

export function CaptureSection({ showAlert }: CaptureSectionProps) {
  const [captureSettings, setCaptureSettings] = useState<CaptureSettingsData | null>(null);
  const [captureStats, setCaptureStats] = useState<CaptureStats | null>(null);
  const [captureLoading, setCaptureLoading] = useState(true);
  const [editedCapture, setEditedCapture] = useState<string>('');
  const [editedRetention, setEditedRetention] = useState<string>('');
  const [captureSaving, setCaptureSaving] = useState(false);

  const fetchCaptureData = useCallback(async () => {
    try {
      setCaptureLoading(true);
      const [settingsRes, statsRes] = await Promise.all([
        adminFetch(`/admin/api/settings`),
        adminFetch(`/admin/api/playground/stats`),
      ]);

      const settingsData = await settingsRes.json();
      const statsData = await statsRes.json();

      if (settingsData.success) {
        setCaptureSettings(settingsData.settings);
        setEditedCapture(settingsData.settings.LLM_PROMPT_CAPTURE.value);
        setEditedRetention(settingsData.settings.LLM_PROMPT_RETENTION_DAYS.value);
      } else {
        showAlert('error', settingsData.error || 'Failed to load settings');
      }

      if (statsData.success) {
        setCaptureStats(statsData.stats);
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    } finally {
      setCaptureLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetchCaptureData();
  }, [fetchCaptureData]);

  const saveCaptureSettings = async () => {
    if (!captureSettings) return;

    setCaptureSaving(true);
    try {
      const updates: Promise<Response>[] = [];

      if (editedCapture !== captureSettings.LLM_PROMPT_CAPTURE.value) {
        updates.push(
          adminFetch(`/admin/api/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'LLM_PROMPT_CAPTURE', value: editedCapture }),
          })
        );
      }

      if (editedRetention !== captureSettings.LLM_PROMPT_RETENTION_DAYS.value) {
        updates.push(
          adminFetch(`/admin/api/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'LLM_PROMPT_RETENTION_DAYS', value: editedRetention }),
          })
        );
      }

      await Promise.all(updates);
      showAlert('success', 'Settings saved');

      // Update local state
      setCaptureSettings((prev) =>
        prev
          ? {
              ...prev,
              LLM_PROMPT_CAPTURE: {
                ...prev.LLM_PROMPT_CAPTURE,
                value: editedCapture,
                is_db_override: true,
              },
              LLM_PROMPT_RETENTION_DAYS: {
                ...prev.LLM_PROMPT_RETENTION_DAYS,
                value: editedRetention,
                is_db_override: true,
              },
            }
          : null
      );
    } catch {
      showAlert('error', 'Failed to save settings');
    } finally {
      setCaptureSaving(false);
    }
  };

  const resetCaptureSettings = async () => {
    setCaptureSaving(true);
    try {
      const response = await adminFetch(`/admin/api/settings/reset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });

      const data = await response.json();

      if (data.success) {
        showAlert('success', 'Settings reset to defaults');
        await fetchCaptureData();
      } else {
        showAlert('error', data.error || 'Failed to reset settings');
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    } finally {
      setCaptureSaving(false);
    }
  };

  const hasCaptureChanges =
    captureSettings &&
    (editedCapture !== captureSettings.LLM_PROMPT_CAPTURE.value ||
      editedRetention !== captureSettings.LLM_PROMPT_RETENTION_DAYS.value);

  if (captureLoading || !captureSettings) {
    return (
      <div className="admin-loading">
        <div className="admin-loading__spinner" />
        <span className="admin-loading__text">Loading settings...</span>
      </div>
    );
  }

  return (
    <div className="us-capture">
      <div className="admin-card">
        <div className="admin-card__header">
          <h3 className="admin-card__title">Capture Mode</h3>
          {captureSettings.LLM_PROMPT_CAPTURE.is_db_override && (
            <span className="admin-badge admin-badge--primary">Custom</span>
          )}
        </div>
        <p className="admin-card__subtitle">{captureSettings.LLM_PROMPT_CAPTURE.description}</p>
        <div className="us-capture__options">
          {captureSettings.LLM_PROMPT_CAPTURE.options?.map((option) => (
            <button
              key={option}
              className={`us-option ${editedCapture === option ? 'us-option--selected' : ''}`}
              onClick={() => setEditedCapture(option)}
              disabled={captureSaving}
              type="button"
            >
              <span className="us-option__label">{formatOptionLabel(option)}</span>
              {option === captureSettings.LLM_PROMPT_CAPTURE.env_default && (
                <span className="us-option__default">default</span>
              )}
            </button>
          ))}
        </div>
      </div>

      <div className="admin-card">
        <div className="admin-card__header">
          <h3 className="admin-card__title">Retention Period</h3>
          {captureSettings.LLM_PROMPT_RETENTION_DAYS.is_db_override && (
            <span className="admin-badge admin-badge--primary">Custom</span>
          )}
        </div>
        <p className="admin-card__subtitle">
          {captureSettings.LLM_PROMPT_RETENTION_DAYS.description}
        </p>
        <div className="us-capture__retention">
          <input
            type="number"
            className="admin-input"
            value={editedRetention}
            onChange={(e) => setEditedRetention(e.target.value)}
            min="0"
            disabled={captureSaving}
            style={{ maxWidth: '120px' }}
          />
          <span className="us-capture__unit">days</span>
          {editedRetention === '0' && (
            <span className="admin-badge admin-badge--warning">Unlimited</span>
          )}
        </div>
      </div>

      {captureStats && (
        <div className="admin-card">
          <h3 className="admin-card__title">Statistics</h3>
          <div className="us-stats">
            <div className="us-stat">
              <span className="us-stat__value">{captureStats.total?.toLocaleString() || 0}</span>
              <span className="us-stat__label">Total Captures</span>
            </div>
          </div>
          {captureStats.by_call_type && Object.keys(captureStats.by_call_type).length > 0 && (
            <div className="us-stat-breakdown">
              <span className="us-stat-breakdown__title">By Call Type</span>
              <div className="us-stat-breakdown__items">
                {Object.entries(captureStats.by_call_type)
                  .sort(([, a], [, b]) => b - a)
                  .slice(0, 5)
                  .map(([type, count]) => (
                    <div key={type} className="us-stat-item">
                      <span className="us-stat-item__label">{type}</span>
                      <span className="us-stat-item__value">{count.toLocaleString()}</span>
                    </div>
                  ))}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="us-capture__actions">
        <button
          type="button"
          className="admin-btn admin-btn--secondary"
          onClick={resetCaptureSettings}
          disabled={captureSaving}
        >
          Reset to Defaults
        </button>
        <button
          type="button"
          className="admin-btn admin-btn--primary"
          onClick={saveCaptureSettings}
          disabled={captureSaving || !hasCaptureChanges}
        >
          {captureSaving ? 'Saving...' : 'Save Changes'}
        </button>
      </div>
    </div>
  );
}
