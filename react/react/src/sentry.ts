// Sentry init: UX session replay + the "report a bug" feedback widget.
//
// Gated entirely on VITE_SENTRY_DSN — when the DSN is absent (local dev, CI,
// anyone who hasn't wired a project) every export below is a cheap no-op, so
// the app behaves exactly as it did before Sentry existed. The DSN is a public
// client identifier (it only permits *sending* events), so it is safe to ship
// in the bundle.
//
// We deliberately do NOT auto-inject Sentry's default feedback button — the
// floating launcher is our own <FeedbackButton/> (matches app styling and the
// portal pattern). This module just owns init + the imperative form trigger.
import * as Sentry from '@sentry/react';

const DSN = import.meta.env.VITE_SENTRY_DSN as string | undefined;

/** True once init() ran against a real DSN — guards the helpers below. */
export const sentryEnabled = Boolean(DSN);

export function initSentry(): void {
  if (!DSN) return;

  Sentry.init({
    dsn: DSN,
    environment: import.meta.env.MODE,
    // Tie a release to the build so replays/errors group by deploy. The CI
    // build passes the git sha through as VITE_SENTRY_RELEASE (optional).
    release: import.meta.env.VITE_SENTRY_RELEASE as string | undefined,
    integrations: [
      Sentry.replayIntegration({
        // Poker has no real PII on the table, and the whole point is to SEE
        // what the user saw — so don't mask. Revisit if a screen ever renders
        // something sensitive (e.g. account email in plain text).
        maskAllText: false,
        blockAllMedia: false,
      }),
      Sentry.feedbackIntegration({
        autoInject: false, // we render our own launcher (FeedbackButton)
        colorScheme: 'dark',
        showBranding: false,
        enableScreenshot: true,
      }),
    ],
    // Record 10% of all sessions, but 100% of any session that hits an error —
    // so every bug report / crash arrives with its replay attached even though
    // baseline sampling stays cheap.
    replaysSessionSampleRate: 0.1,
    replaysOnErrorSampleRate: 1.0,
    tracesSampleRate: 0.1,
  });
}

/**
 * Open the Sentry user-feedback form imperatively (called by our own floating
 * button). No-op when Sentry is disabled. The form auto-attaches the active
 * session replay, recent console/network breadcrumbs, and whatever user/tag
 * context we've set (see setSentryUser / setSentryGame).
 */
export async function openFeedbackForm(): Promise<void> {
  const feedback = Sentry.getFeedback();
  if (!feedback) return;
  const form = await feedback.createForm();
  form.appendToDom();
  form.open();
}

/** Attach the signed-in identity so reports/replays are searchable by player. */
export function setSentryUser(user: { id: string; name: string } | null): void {
  if (!sentryEnabled) return;
  Sentry.setUser(user ? { id: user.id, username: user.name } : null);
}

/** Tag the current game so a report links straight to its admin debug views. */
export function setSentryGame(gameId: string | null): void {
  if (!sentryEnabled) return;
  Sentry.setTag('game_id', gameId ?? undefined);
}
