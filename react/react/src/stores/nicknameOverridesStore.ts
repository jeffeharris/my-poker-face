import { useCallback } from 'react';
import { create } from 'zustand';

interface NicknameOverridesStore {
  overrides: Record<string, string>;
  setAll: (map: Record<string, string>) => void;
  setOne: (name: string, value: string | null) => void;
  reset: () => void;
}

export const useNicknameOverridesStore = create<NicknameOverridesStore>((set) => ({
  overrides: {},
  setAll: (map) => set({ overrides: { ...map } }),
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
    (player: NicknameSubject) =>
      overrides[player.name] || player.nickname || player.name,
    [overrides],
  );
}
