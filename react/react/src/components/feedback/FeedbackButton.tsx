import { createPortal } from 'react-dom';
import { useLocation } from 'react-router-dom';
import { MessageSquareWarning } from 'lucide-react';
import { sentryEnabled, openFeedbackForm } from '../../sentry';
import { useAuth } from '../../hooks/useAuth';
import { logger } from '../../utils/logger';
import './FeedbackButton.css';

/**
 * Routes that show a poker action bar (Fold/Call/Raise) at the bottom, where the
 * launcher must sit higher so it never overlaps the controls. Everywhere else it
 * rests in the bottom-left corner.
 */
function isInGame(pathname: string): boolean {
  return (
    (pathname.startsWith('/game/') && !pathname.startsWith('/game/new')) ||
    pathname === '/cash' ||
    pathname.startsWith('/tournament')
  );
}

/**
 * App-wide "report a bug" launcher. Floats over the authenticated app and, on
 * click, opens the Sentry user-feedback form — which auto-attaches the active
 * session replay plus recent console/network breadcrumbs and our user/game
 * context.
 *
 * Renders nothing when Sentry is disabled (no VITE_SENTRY_DSN) or before the
 * user is signed in (no feedback from the anonymous landing page). Portaled to
 * <body> so the fixed launcher escapes ancestor stacking contexts (see
 * BottomSheet).
 */
export function FeedbackButton() {
  const { isAuthenticated } = useAuth();
  const { pathname } = useLocation();
  if (!sentryEnabled || !isAuthenticated) return null;

  return createPortal(
    <button
      type="button"
      className={`feedback-button${isInGame(pathname) ? ' feedback-button--in-game' : ''}`}
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
