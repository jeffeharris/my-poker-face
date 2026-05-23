import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import {
  useNicknameOverridesStore,
  useDisplayNickname,
} from './nicknameOverridesStore';

beforeEach(() => {
  useNicknameOverridesStore.getState().reset();
});

describe('useNicknameOverridesStore', () => {
  describe('setOne', () => {
    it('adds a non-empty override', () => {
      useNicknameOverridesStore.getState().setOne('Batman', 'the dark one');
      expect(useNicknameOverridesStore.getState().overrides).toEqual({
        Batman: 'the dark one',
      });
    });

    it('trims whitespace before storing', () => {
      useNicknameOverridesStore.getState().setOne('Batman', '  spaced  ');
      expect(useNicknameOverridesStore.getState().overrides.Batman).toBe('spaced');
    });

    it('deletes the entry when given an empty string', () => {
      const { setOne } = useNicknameOverridesStore.getState();
      setOne('Batman', 'x');
      setOne('Batman', '');
      expect(useNicknameOverridesStore.getState().overrides).not.toHaveProperty('Batman');
    });

    it('deletes the entry when given whitespace only', () => {
      const { setOne } = useNicknameOverridesStore.getState();
      setOne('Batman', 'x');
      setOne('Batman', '   \t\n  ');
      expect(useNicknameOverridesStore.getState().overrides).not.toHaveProperty('Batman');
    });

    it('deletes the entry when given null', () => {
      const { setOne } = useNicknameOverridesStore.getState();
      setOne('Batman', 'x');
      setOne('Batman', null);
      expect(useNicknameOverridesStore.getState().overrides).not.toHaveProperty('Batman');
    });

    it('overwrites an existing override', () => {
      const { setOne } = useNicknameOverridesStore.getState();
      setOne('Batman', 'first');
      setOne('Batman', 'second');
      expect(useNicknameOverridesStore.getState().overrides.Batman).toBe('second');
    });
  });

  describe('hydrate', () => {
    it('populates an empty store from a server map', () => {
      useNicknameOverridesStore.getState().hydrate({ Batman: 'a', Joker: 'b' });
      expect(useNicknameOverridesStore.getState().overrides).toEqual({
        Batman: 'a',
        Joker: 'b',
      });
    });

    it('preserves a local edit that beat an in-flight server fetch', () => {
      // Simulates: App.tsx fires fetchNicknameOverrides → user opens
      // dossier and renames Batman → fetch resolves and hydrate runs.
      // The user's edit must win over the (older) server value.
      const { setOne, hydrate } = useNicknameOverridesStore.getState();
      setOne('Batman', 'my new alias');
      hydrate({ Batman: 'stale server value', Joker: 'kept' });
      expect(useNicknameOverridesStore.getState().overrides).toEqual({
        Batman: 'my new alias',
        Joker: 'kept',
      });
    });

    it('soft-fails: re-hydrating with {} after a network error keeps local edits', () => {
      const { setOne, hydrate } = useNicknameOverridesStore.getState();
      setOne('Batman', 'local');
      hydrate({});
      expect(useNicknameOverridesStore.getState().overrides).toEqual({
        Batman: 'local',
      });
    });
  });

  describe('reset', () => {
    it('clears every override', () => {
      const { setOne, reset } = useNicknameOverridesStore.getState();
      setOne('Batman', 'a');
      setOne('Joker', 'b');
      reset();
      expect(useNicknameOverridesStore.getState().overrides).toEqual({});
    });
  });
});

describe('useDisplayNickname', () => {
  it('returns the override when one is set', () => {
    useNicknameOverridesStore.getState().setOne('Batman', 'my dark one');
    const { result } = renderHook(() => useDisplayNickname());
    expect(result.current({ name: 'Batman', nickname: 'The Dark Knight' })).toBe(
      'my dark one',
    );
  });

  it('falls back to nickname when no override exists', () => {
    const { result } = renderHook(() => useDisplayNickname());
    expect(result.current({ name: 'Batman', nickname: 'The Dark Knight' })).toBe(
      'The Dark Knight',
    );
  });

  it('falls back to name when both override and nickname are missing', () => {
    const { result } = renderHook(() => useDisplayNickname());
    expect(result.current({ name: 'Batman' })).toBe('Batman');
  });

  it('treats null nickname as missing and falls through to name', () => {
    const { result } = renderHook(() => useDisplayNickname());
    expect(result.current({ name: 'Batman', nickname: null })).toBe('Batman');
  });

  it('treats empty-string nickname as missing and falls through to name', () => {
    const { result } = renderHook(() => useDisplayNickname());
    expect(result.current({ name: 'Batman', nickname: '' })).toBe('Batman');
  });

  it('respects the override even when the nickname is also set', () => {
    useNicknameOverridesStore.getState().setOne('Batman', 'override');
    const { result } = renderHook(() => useDisplayNickname());
    expect(result.current({ name: 'Batman', nickname: 'canonical' })).toBe('override');
  });
});
