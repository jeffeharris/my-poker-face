import { createPortal } from 'react-dom';
import { MessageSquareWarning } from 'lucide-react';
import { sentryEnabled, openFeedbackForm } from '../../sentry';
import { logger } from '../../utils/logger';
import './FeedbackButton.css';

/**
 * App-wide "report a bug" launcher. Floats over every route and, on click,
 * opens the Sentry user-feedback form — which auto-attaches the active session
 * replay plus recent console/network breadcrumbs and our user/game context.
 *
 * Renders nothing when Sentry is disabled (no VITE_SENTRY_DSN), so local dev
 * and any DSN-less build show no orphan button. Portaled to <body> so the
 * fixed launcher escapes ancestor stacking contexts (see BottomSheet).
 */
export function FeedbackButton() {
  if (!sentryEnabled) return null;

  return createPortal(
    <button
      type="button"
      className="feedback-button"
      aria-label="Report a bug or send feedback"
      title="Report a bug or send feedback"
      onClick={() => {
        openFeedbackForm().catch((e) => logger.error('[feedback] failed to open form', e));
      }}
    >
      <MessageSquareWarning size={20} aria-hidden="true" />
    </button>,
    document.body
  );
}
