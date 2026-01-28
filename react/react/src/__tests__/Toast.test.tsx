/**
 * T1-15: Verify react-hot-toast Toaster is rendered in App.
 */
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
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
