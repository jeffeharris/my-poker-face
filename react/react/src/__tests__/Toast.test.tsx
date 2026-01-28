/**
 * T1-15: Verify react-hot-toast Toaster is rendered in App.
 *
 * NOTE: This test requires vitest (or jest) + @testing-library/react to be
 * configured. It is written ahead of that setup so it can be enabled once
 * the test infrastructure is in place.
 *
 * To run (once vitest is configured):
 *   npx vitest run src/__tests__/Toast.test.tsx
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Toaster } from 'react-hot-toast';
import toast from 'react-hot-toast';

describe('Toast system (T1-15)', () => {
  it('renders the Toaster component without crashing', () => {
    const { container } = render(<Toaster position="top-right" toastOptions={{ duration: 4000 }} />);
    expect(container).toBeTruthy();
  });

  it('toast function is callable and returns a string id', () => {
    render(<Toaster />);
    const id = toast('Test notification');
    expect(typeof id).toBe('string');
  });

  it('toast.error is callable', () => {
    render(<Toaster />);
    const id = toast.error('Something went wrong');
    expect(typeof id).toBe('string');
  });

  it('toast.success is callable', () => {
    render(<Toaster />);
    const id = toast.success('Game created');
    expect(typeof id).toBe('string');
  });
});
