import { useState, useEffect, useCallback } from 'react';
import { adminFetch } from '../../../../utils/api';
import type { StorageStats, ShowAlert } from '../types';
import './StorageSection.css';

interface StorageSectionProps {
  showAlert: ShowAlert;
}

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

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(i > 1 ? 2 : 0)} ${sizes[i]}`;
}

export function StorageSection({ showAlert }: StorageSectionProps) {
  const [storage, setStorage] = useState<StorageStats | null>(null);
  const [storageLoading, setStorageLoading] = useState(true);

  const fetchStorage = useCallback(async () => {
    try {
      setStorageLoading(true);
      const response = await adminFetch(`/admin/api/settings/storage`);
      const data = await response.json();

      if (data.success) {
        setStorage(data.storage);
      } else {
        showAlert('error', data.error || 'Failed to load storage stats');
      }
    } catch {
      showAlert('error', 'Failed to connect to server');
    } finally {
      setStorageLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetchStorage();
  }, [fetchStorage]);

  if (storageLoading || !storage) {
    return (
      <div className="admin-loading">
        <div className="admin-loading__spinner" />
        <span className="admin-loading__text">Loading storage stats...</span>
      </div>
    );
  }

  return (
    <div className="us-storage">
      <div className="us-storage__total">
        <span className="us-storage__total-value">{storage.total_mb.toFixed(2)}</span>
        <span className="us-storage__total-unit">MB</span>
        <span className="us-storage__total-label">Total Database Size</span>
      </div>

      <div className="us-storage__breakdown">
        {Object.entries(storage.categories)
          .sort(([, a], [, b]) => b.bytes - a.bytes)
          .map(([category, stats]) => (
            <div key={category} className="us-storage__category">
              <div className="us-storage__category-header">
                <span className="us-storage__category-name">{formatCategoryName(category)}</span>
                <span className="us-storage__category-size">{formatBytes(stats.bytes)}</span>
              </div>
              <div className="us-storage__bar">
                <div
                  className={`us-storage__bar-fill us-storage__bar-fill--${category}`}
                  style={{ width: `${Math.max(stats.percentage, 1)}%` }}
                />
              </div>
              <div className="us-storage__category-meta">
                <span>{stats.rows.toLocaleString()} rows</span>
                <span>{stats.percentage.toFixed(1)}%</span>
              </div>
            </div>
          ))}
      </div>
    </div>
  );
}
