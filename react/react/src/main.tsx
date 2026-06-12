import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { AuthProvider } from './hooks/useAuth';
import { DeckPackProvider } from './hooks/useDeckPack';
import { UsageStatsProvider } from './hooks/UsageStatsProvider';
import { installCsrfFetch } from './utils/csrf';
import { initSentry } from './sentry';
import { initSafeAreaCapture } from './utils/safeArea';
import { isNativePlatform } from './utils/nativeAuth';
import './styles/fonts.css';
import './index.css';
import App from './App.tsx';

// Initialize Sentry (UX session replay + bug-report widget) first, so the
// replay buffer and error hooks are armed before any app code runs. No-op
// unless VITE_SENTRY_DSN is set, so local dev stays quiet.
initSentry();

// PRH-36: attach the X-CSRF-Token header to mutating API requests, before any
// fetch fires. Must run before the providers below (which fetch on mount).
installCsrfFetch();

// Capture the device's bottom safe-area inset into --app-safe-bottom (stable
// across keyboard show/hide, unlike live env()). Bottom-anchored UI uses it so
// the iOS keyboard can't leave the action bar clipped by the home indicator.
initSafeAreaCapture();

// Self-heal stale-deploy chunk failures. After a deploy, a client running the
// previous build (or a stale PWA cache) can request a lazily-imported route
// chunk whose hashed filename changed — Vite then fires `vite:preloadError`
// ("Unable to preload CSS/JS for /assets/…"). Reload once to fetch the fresh
// asset graph. A short sessionStorage cooldown prevents a reload loop when an
// asset is genuinely broken (rather than just stale).
window.addEventListener('vite:preloadError', () => {
  const KEY = 'vite-preload-reloaded-at';
  const last = Number(sessionStorage.getItem(KEY) || '0');
  if (Date.now() - last > 10_000) {
    sessionStorage.setItem(KEY, String(Date.now()));
    window.location.reload();
  }
  // Within the cooldown window we let the error surface to the ErrorBoundary
  // rather than loop — a persistent failure is a real bug, not a stale cache.
});

function render() {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <BrowserRouter>
        <AuthProvider>
          <UsageStatsProvider>
            <DeckPackProvider>
              <App />
            </DeckPackProvider>
          </UsageStatsProvider>
        </AuthProvider>
      </BrowserRouter>
    </StrictMode>
  );
}

// On native, wire token storage + Google sign-in before rendering so the auth
// bootstrap (which loads persisted tokens) finds them. On web this is a no-op
// and renders immediately — the dynamic import is never reached.
if (isNativePlatform()) {
  import('./native/bootstrap')
    .then(({ initNative }) => initNative())
    .catch(() => {
      // Native init failure shouldn't block the app — render logged-out.
    })
    .finally(render);
} else {
  render();
}
