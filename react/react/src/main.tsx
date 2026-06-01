import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { AuthProvider } from './hooks/useAuth';
import { DeckPackProvider } from './hooks/useDeckPack';
import { UsageStatsProvider } from './hooks/UsageStatsProvider';
import { installCsrfFetch } from './utils/csrf';
import './index.css';
import App from './App.tsx';

// PRH-36: attach the X-CSRF-Token header to mutating API requests, before any
// fetch fires. Must run before the providers below (which fetch on mount).
installCsrfFetch();

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
