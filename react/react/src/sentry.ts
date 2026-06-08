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
import { config } from './config';

const DSN = import.meta.env.VITE_SENTRY_DSN as string | undefined;

/** True once init() ran against a real DSN — guards the helpers below. */
export const sentryEnabled = Boolean(DSN);

export function initSentry(): void {
  if (!DSN) return;

  Sentry.init({
    dsn: DSN,
    // Route envelopes through our own backend instead of *.ingest.sentry.io, so
    // ad/tracker blockers (uBlock, Brave shields, etc.) can't silently drop our
    // users' errors, replays, and feedback. The backend forwards to Sentry —
    // see flask_app/routes/sentry_relay_routes.py. In prod config.API_URL is ''
    // (same-origin → '/api/event-relay'); in dev it points at the API host.
    tunnel: `${config.API_URL}/api/event-relay`,
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

  // Pull the client-exposed feature flags and attach them to the Sentry scope,
  // so errors / replays / bug reports show which player-facing flags were live.
  // Fire-and-forget: a failure here must never block app startup.
  void loadFeatureFlagsIntoSentry();
}

/** Fetch the client-exposed flags and stamp them onto the Sentry scope. */
async function loadFeatureFlagsIntoSentry(): Promise<void> {
  try {
    const res = await fetch(`${config.API_URL}/api/feature-flags`, {
      credentials: 'include',
    });
    if (!res.ok) return;
    const flags = (await res.json()) as Record<string, boolean>;
    Sentry.setContext('feature_flags', flags);
  } catch {
    // best-effort context only — ignore
  }
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

/**
 * Attach the signed-in identity so reports/replays/errors are searchable by
 * player. Email is included so the feedback form prefills it (and so you can
 * reach the reporter); `user_type` is a tag for guest-vs-registered filtering.
 */
export function setSentryUser(
  user: { id: string; name: string; email?: string; isGuest?: boolean } | null
): void {
  if (!sentryEnabled) return;
  if (!user) {
    Sentry.setUser(null);
    Sentry.setTag('user_type', undefined);
    return;
  }
  Sentry.setUser({
    id: user.id,
    username: user.name,
    ...(user.email ? { email: user.email } : {}),
  });
  Sentry.setTag('user_type', user.isGuest ? 'guest' : 'registered');
}

/** Tag the current game so a report links straight to its admin debug views. */
export function setSentryGame(gameId: string | null): void {
  if (!sentryEnabled) return;
  Sentry.setTag('game_id', gameId ?? undefined);
}

/**
 * Stop session-replay recording for admins (i.e. us, dogfooding), so our own
 * testing sessions don't burn the limited free-tier replay quota or add noise.
 * Errors/feedback are still captured — only the replay recording is dropped.
 *
 * One-way + best-effort: once stopped it stays stopped for this page load. We
 * deliberately don't auto-restart on a later identity swap, because start()
 * would begin recording a *full* session (overriding our on-error-only
 * sampling); a page reload re-evaluates cleanly for whoever is signed in.
 */
export function suppressReplayForAdmin(isAdmin: boolean): void {
  if (!sentryEnabled || !isAdmin) return;
  try {
    void Sentry.getReplay()?.stop();
  } catch {
    // best-effort — never let replay control throw into the app
  }
}
