import { useCallback } from 'react';
import { create } from 'zustand';

interface NicknameOverridesStore {
  overrides: Record<string, string>;
  /**
   * Merge a server-fetched override map into the store. Any local
   * edits applied since the fetch started are preserved (they win
   * over the server value for the same key), so a user who renames
   * an opponent while the initial fetch is in flight doesn't see
   * their edit clobbered when the fetch resolves.
   *
   * App.tsx pairs this with a `reset()` on identity change, so a
   * stale prior-user override can never leak into the merge —
   * the store is empty when hydrate runs after a real login.
   */
  hydrate: (map: Record<string, string>) => void;
  setOne: (name: string, value: string | null) => void;
  reset: () => void;
}

export const useNicknameOverridesStore = create<NicknameOverridesStore>((set) => ({
  overrides: {},
  hydrate: (map) => set((state) => ({ overrides: { ...map, ...state.overrides } })),
  setOne: (name, value) =>
    set((state) => {
      const next = { ...state.overrides };
      const trimmed = value?.trim() ?? '';
      if (trimmed) {
        next[name] = trimmed;
      } else {
        delete next[name];
      }
      return { overrides: next };
    }),
  reset: () => set({ overrides: {} }),
}));

type NicknameSubject = { name: string; nickname?: string | null };

export function useDisplayNickname(): (player: NicknameSubject) => string {
  const overrides = useNicknameOverridesStore((s) => s.overrides);
  return useCallback(
    (player: NicknameSubject) => overrides[player.name] || player.nickname || player.name,
    [overrides]
  );
}
