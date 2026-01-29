import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react';
import { setActivePack as setCardsActivePack } from '../utils/cards';
import { DECK_PACKS } from './deckPacks';
import type { DeckPack } from './deckPacks';

const STORAGE_KEY = 'deckPack';
const DEFAULT_PACK = 'classic';

function getStoredPack(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) || DEFAULT_PACK;
  } catch {
    return DEFAULT_PACK;
  }
}

// ============================================
// Context
// ============================================

interface DeckPackContextValue {
  activePackId: string;
  activePack: DeckPack;
  setPackId: (id: string) => void;
}

const DeckPackContext = createContext<DeckPackContextValue | null>(null);

export function DeckPackProvider({ children }: { children: ReactNode }) {
  const [activePackId, setActivePackId] = useState(getStoredPack);

  const setPackId = useCallback((id: string) => {
    const pack = DECK_PACKS.find(p => p.id === id);
    if (!pack) return;
    setActivePackId(id);
    try {
      localStorage.setItem(STORAGE_KEY, id);
    } catch {
      // localStorage unavailable
    }
  }, []);

  // Keep the cards.ts module-level state in sync
  useEffect(() => {
    setCardsActivePack(activePackId);
  }, [activePackId]);

  const activePack = DECK_PACKS.find(p => p.id === activePackId) || DECK_PACKS[0];

  return (
    <DeckPackContext.Provider value={{ activePackId, activePack, setPackId }}>
      {children}
    </DeckPackContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useDeckPack(): DeckPackContextValue {
  const ctx = useContext(DeckPackContext);
  if (!ctx) {
    // Fallback for use outside provider (e.g., in utilities)
    return {
      activePackId: getStoredPack(),
      activePack: DECK_PACKS.find(p => p.id === getStoredPack()) || DECK_PACKS[0],
      setPackId: () => {},
    };
  }
  return ctx;
}
