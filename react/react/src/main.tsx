import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { DeckPackProvider } from './hooks/useDeckPack'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <DeckPackProvider>
        <App />
      </DeckPackProvider>
    </BrowserRouter>
  </StrictMode>,
)
