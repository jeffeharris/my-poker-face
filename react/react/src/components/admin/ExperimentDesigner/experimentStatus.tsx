/**
 * Shared experiment status configuration.
 * Used by ExperimentList and ExperimentDetail for consistent status display.
 */

import { Clock, CheckCircle, XCircle, Loader2, Pause, AlertTriangle } from 'lucide-react';

export type ExperimentStatus = 'pending' | 'running' | 'completed' | 'failed' | 'paused' | 'interrupted';

interface StatusConfigItem {
  icon: React.ReactNode;
  className: string;
  label: string;
}

/**
 * Create status configuration with customizable icon size.
 * @param iconSize - Size in pixels for the icons (default: 16)
 */
// eslint-disable-next-line react-refresh/only-export-components
export function createStatusConfig(iconSize: number = 16): Record<ExperimentStatus, StatusConfigItem> {
  return {
    pending: {
      icon: <Clock size={iconSize} />,
      className: 'status-badge--pending',
      label: 'Pending',
    },
    running: {
      icon: <Loader2 size={iconSize} className="animate-spin" />,
      className: 'status-badge--running',
      label: 'Running',
    },
    completed: {
      icon: <CheckCircle size={iconSize} />,
      className: 'status-badge--completed',
      label: 'Completed',
    },
    failed: {
      icon: <XCircle size={iconSize} />,
      className: 'status-badge--failed',
      label: 'Failed',
    },
    paused: {
      icon: <Pause size={iconSize} />,
      className: 'status-badge--paused',
      label: 'Paused',
    },
    interrupted: {
      icon: <AlertTriangle size={iconSize} />,
      className: 'status-badge--interrupted',
      label: 'Interrupted',
    },
  };
}

// Pre-configured sizes for common use cases
export const STATUS_CONFIG_LARGE = createStatusConfig(16);
export const STATUS_CONFIG_SMALL = createStatusConfig(14);
