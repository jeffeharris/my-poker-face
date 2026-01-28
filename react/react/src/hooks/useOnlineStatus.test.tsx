/**
 * Test for useOnlineStatus hook.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useOnlineStatus } from './useOnlineStatus'
import toast from 'react-hot-toast'

// Mock react-hot-toast
vi.mock('react-hot-toast', () => ({
  default: {
    error: vi.fn(() => 'toast-id-1'),
    success: vi.fn(),
    dismiss: vi.fn(),
  },
}))

describe('useOnlineStatus', () => {
  let originalOnLine: boolean

  beforeEach(() => {
    originalOnLine = navigator.onLine
    vi.clearAllMocks()
  })

  afterEach(() => {
    Object.defineProperty(navigator, 'onLine', {
      value: originalOnLine,
      writable: true,
      configurable: true,
    })
  })

  it('returns true when browser is online', () => {
    Object.defineProperty(navigator, 'onLine', { value: true, configurable: true })
    const { result } = renderHook(() => useOnlineStatus())
    expect(result.current).toBe(true)
  })

  it('returns false when browser is offline', () => {
    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true })
    const { result } = renderHook(() => useOnlineStatus())
    expect(result.current).toBe(false)
  })

  it('shows error toast when going offline', () => {
    Object.defineProperty(navigator, 'onLine', { value: true, configurable: true })
    const { result } = renderHook(() => useOnlineStatus())

    act(() => {
      window.dispatchEvent(new Event('offline'))
    })

    expect(result.current).toBe(false)
    expect(toast.error).toHaveBeenCalledWith(
      'Connection lost. Some features may not work.',
      expect.objectContaining({ duration: Infinity, id: 'offline-status' })
    )
  })

  it('dismisses offline toast and shows success toast when coming back online', () => {
    Object.defineProperty(navigator, 'onLine', { value: true, configurable: true })
    const { result } = renderHook(() => useOnlineStatus())

    // Go offline first
    act(() => {
      window.dispatchEvent(new Event('offline'))
    })

    // Come back online
    act(() => {
      window.dispatchEvent(new Event('online'))
    })

    expect(result.current).toBe(true)
    expect(toast.dismiss).toHaveBeenCalledWith('toast-id-1')
    expect(toast.success).toHaveBeenCalledWith(
      'Back online',
      expect.objectContaining({ duration: 3000, id: 'online-status' })
    )
  })

  it('cleans up event listeners on unmount', () => {
    const removeEventListenerSpy = vi.spyOn(window, 'removeEventListener')
    const { unmount } = renderHook(() => useOnlineStatus())

    unmount()

    expect(removeEventListenerSpy).toHaveBeenCalledWith('offline', expect.any(Function))
    expect(removeEventListenerSpy).toHaveBeenCalledWith('online', expect.any(Function))
    removeEventListenerSpy.mockRestore()
  })
})
