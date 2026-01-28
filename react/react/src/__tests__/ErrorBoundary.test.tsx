/**
 * T1-11: Verify ErrorBoundary catches render errors and shows fallback UI.
 *
 * NOTE: This test requires vitest (or jest) + @testing-library/react to be
 * configured. It is written ahead of that setup so it can be enabled once
 * the test infrastructure is in place.
 *
 * To run (once vitest is configured):
 *   npx vitest run src/__tests__/ErrorBoundary.test.tsx
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ErrorBoundary } from '../components/ErrorBoundary';

// A component that always throws during render
function ThrowingComponent(): never {
  throw new Error('Test render error');
}

function GoodComponent() {
  return <div>All good</div>;
}

describe('ErrorBoundary (T1-11)', () => {
  // Suppress React error boundary console.error noise in test output
  const originalConsoleError = console.error;
  beforeEach(() => {
    console.error = vi.fn();
  });
  afterEach(() => {
    console.error = originalConsoleError;
  });

  it('renders children when no error occurs', () => {
    render(
      <ErrorBoundary>
        <GoodComponent />
      </ErrorBoundary>
    );
    expect(screen.getByText('All good')).toBeTruthy();
  });

  it('shows fallback UI when a child component throws', () => {
    render(
      <ErrorBoundary>
        <ThrowingComponent />
      </ErrorBoundary>
    );
    expect(screen.getByText('Something went wrong')).toBeTruthy();
    expect(screen.getByText('Reload')).toBeTruthy();
  });

  it('shows custom fallback action button when provided', () => {
    const onAction = vi.fn();
    render(
      <ErrorBoundary fallbackAction={{ label: 'Return to Menu', onClick: onAction }}>
        <ThrowingComponent />
      </ErrorBoundary>
    );
    expect(screen.getByText('Return to Menu')).toBeTruthy();
  });

  it('does not show custom action button when not provided', () => {
    render(
      <ErrorBoundary>
        <ThrowingComponent />
      </ErrorBoundary>
    );
    expect(screen.queryByText('Return to Menu')).toBeNull();
  });
});
