import { useState, useEffect, useCallback } from 'react';
import { adminFetch } from '../../../../utils/api';
import type { WebhookSetting, ShowAlert } from '../types';

interface AlertingSectionProps {
  showAlert: ShowAlert;
}

export function AlertingSection({ showAlert }: AlertingSectionProps) {
  // The URL is a secret — we only ever hold the masked value from the server;
  // the input is what the admin is setting.
  const [alertingSetting, setAlertingSetting] = useState<WebhookSetting | null>(null);
  const [alertingLoading, setAlertingLoading] = useState(true);
  const [webhookInput, setWebhookInput] = useState('');
  const [webhookSaving, setWebhookSaving] = useState(false);

  const fetchAlertingData = useCallback(async () => {
    try {
      setAlertingLoading(true);
      const res = await adminFetch(`/admin/api/settings`);
      const data = await res.json();
      if (data.success && data.settings?.ALERT_WEBHOOK_URL) {
        setAlertingSetting(data.settings.ALERT_WEBHOOK_URL as WebhookSetting);
      } else {
        showAlert('error', data.error || 'Failed to load alerting settings');
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    } finally {
      setAlertingLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetchAlertingData();
  }, [fetchAlertingData]);

  const saveWebhook = async () => {
    const value = webhookInput.trim();
    if (!value) return;
    setWebhookSaving(true);
    try {
      const res = await adminFetch(`/admin/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: 'ALERT_WEBHOOK_URL', value }),
      });
      const data = await res.json();
      if (data.success) {
        showAlert('success', 'Alert webhook saved');
        setWebhookInput('');
        await fetchAlertingData();
      } else {
        showAlert('error', data.error || 'Failed to save webhook');
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    } finally {
      setWebhookSaving(false);
    }
  };

  const clearWebhook = async () => {
    setWebhookSaving(true);
    try {
      const res = await adminFetch(`/admin/api/settings/reset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: 'ALERT_WEBHOOK_URL' }),
      });
      const data = await res.json();
      if (data.success) {
        showAlert('success', 'Alert webhook cleared (falls back to env if set)');
        setWebhookInput('');
        await fetchAlertingData();
      } else {
        showAlert('error', data.error || 'Failed to clear webhook');
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    } finally {
      setWebhookSaving(false);
    }
  };

  if (alertingLoading || !alertingSetting) {
    return (
      <div className="admin-loading">
        <div className="admin-loading__spinner" />
        <span className="admin-loading__text">Loading settings...</span>
      </div>
    );
  }

  const configured = !!alertingSetting.configured;
  return (
    <div className="us-capture">
      <div className="admin-card">
        <div className="admin-card__header">
          <h3 className="admin-card__title">Alert Webhook</h3>
          <span
            className={`admin-badge ${configured ? 'admin-badge--primary' : 'admin-badge--warning'}`}
          >
            {configured ? 'Configured' : 'Not set'}
          </span>
        </div>
        <p className="admin-card__subtitle">{alertingSetting.description}</p>

        {configured && (
          <p className="admin-card__subtitle">
            Current: <code>{alertingSetting.value}</code>
            {alertingSetting.is_db_override ? ' (admin setting)' : ' (from environment)'}
          </p>
        )}

        <div className="us-capture__retention">
          <input
            type="url"
            className="admin-input"
            placeholder="https://hooks.slack.com/services/…"
            value={webhookInput}
            onChange={(e) => setWebhookInput(e.target.value)}
            disabled={webhookSaving}
            style={{ maxWidth: '420px', flex: 1 }}
          />
        </div>
        <p className="admin-card__subtitle">
          Slack incoming webhook, or a Discord webhook with <code>/slack</code> appended. Saving
          stores it securely (shown masked); leave blank to keep the current value.
        </p>
      </div>

      <div className="us-capture__actions">
        <button
          type="button"
          className="admin-btn admin-btn--secondary"
          onClick={clearWebhook}
          disabled={webhookSaving || !configured}
        >
          Clear
        </button>
        <button
          type="button"
          className="admin-btn admin-btn--primary"
          onClick={saveWebhook}
          disabled={webhookSaving || !webhookInput.trim()}
        >
          {webhookSaving ? 'Saving...' : 'Save Webhook'}
        </button>
      </div>
    </div>
  );
}
