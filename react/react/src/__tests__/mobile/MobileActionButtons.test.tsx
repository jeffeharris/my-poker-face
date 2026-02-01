import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MobileActionButtons } from '../../components/mobile/MobileActionButtons';
import type { BettingContext } from '../../types/game';

function makeProps(overrides: Partial<Parameters<typeof MobileActionButtons>[0]> = {}) {
  const defaults = {
    playerOptions: ['fold', 'call', 'raise'],
    currentPlayerStack: 2000,
    highestBet: 50,
    currentPlayerBet: 0,
    minRaise: 50,
    bigBlind: 50,
    potSize: 150,
    onAction: vi.fn(),
    onQuickChat: vi.fn(),
    bettingContext: {
      player_stack: 2000,
      player_current_bet: 0,
      highest_bet: 50,
      pot_total: 150,
      min_raise_amount: 50,
      available_actions: ['fold', 'call', 'raise'],
      cost_to_call: 50,
      min_raise_to: 100,
      max_raise_to: 2000,
      effective_stack: 1950,
    } as BettingContext,
  };
  return { ...defaults, ...overrides };
}

describe('VT-01: MobileActionButtons renders correct buttons for each option set', () => {
  it('Case 1: fold/call/raise — renders Fold, Call, Raise, and Chat buttons', () => {
    const props = makeProps({ playerOptions: ['fold', 'call', 'raise'] });
    render(<MobileActionButtons {...props} />);

    expect(screen.getByText('Fold')).toBeTruthy();
    expect(screen.getByText(/Call/)).toBeTruthy();
    expect(screen.getByText('Raise')).toBeTruthy();
    expect(screen.getByText('Chat')).toBeTruthy();

    // Call button shows amount
    expect(screen.getByText(/Call \$50/)).toBeTruthy();

    // Check button should NOT be present
    expect(screen.queryByText('Check')).toBeNull();
  });

  it('Case 2: fold/check/raise — renders Fold, Check, Raise; no Call button', () => {
    const props = makeProps({
      playerOptions: ['fold', 'check', 'raise'],
      bettingContext: {
        player_stack: 2000,
        player_current_bet: 50,
        highest_bet: 50,
        pot_total: 150,
        min_raise_amount: 50,
        available_actions: ['fold', 'check', 'raise'],
        cost_to_call: 0,
        min_raise_to: 100,
        max_raise_to: 2050,
        effective_stack: 2000,
      },
    });
    render(<MobileActionButtons {...props} />);

    expect(screen.getByText('Fold')).toBeTruthy();
    expect(screen.getByText('Check')).toBeTruthy();
    expect(screen.getByText('Raise')).toBeTruthy();

    // Call button should NOT be present
    expect(screen.queryByText(/^Call/)).toBeNull();
  });

  it('Case 3: fold/all_in — renders Fold and All-In; no Raise button', () => {
    const props = makeProps({
      playerOptions: ['fold', 'all_in'],
      currentPlayerStack: 80,
      bettingContext: {
        player_stack: 80,
        player_current_bet: 0,
        highest_bet: 50,
        pot_total: 150,
        min_raise_amount: 50,
        available_actions: ['fold', 'all_in'],
        cost_to_call: 50,
        min_raise_to: 100,
        max_raise_to: 80,
        effective_stack: 30,
      },
    });
    render(<MobileActionButtons {...props} />);

    expect(screen.getByText('Fold')).toBeTruthy();
    expect(screen.getByText(/All-In/)).toBeTruthy();

    // Raise button should NOT be present
    expect(screen.queryByText('Raise')).toBeNull();
  });

  it('Case 4: empty playerOptions — no action buttons rendered', () => {
    const props = makeProps({
      playerOptions: [],
      onQuickChat: undefined,
      bettingContext: {
        player_stack: 2000,
        player_current_bet: 0,
        highest_bet: 50,
        pot_total: 150,
        min_raise_amount: 50,
        available_actions: [],
        cost_to_call: 0,
        min_raise_to: 100,
        max_raise_to: 2000,
        effective_stack: 2000,
      },
    });
    const { container } = render(<MobileActionButtons {...props} />);

    // No action buttons should be rendered
    expect(screen.queryByText('Fold')).toBeNull();
    expect(screen.queryByText('Check')).toBeNull();
    expect(screen.queryByText(/^Call/)).toBeNull();
    expect(screen.queryByText('Raise')).toBeNull();
    expect(screen.queryByText(/All-In/)).toBeNull();
    expect(screen.queryByText('Chat')).toBeNull();

    // Container should have the wrapper but no button children
    const buttons = container.querySelectorAll('.action-btn');
    expect(buttons.length).toBe(0);
  });

  it('Chat button is present when onQuickChat is provided', () => {
    const props = makeProps({ onQuickChat: vi.fn() });
    render(<MobileActionButtons {...props} />);
    expect(screen.getByText('Chat')).toBeTruthy();
  });

  it('Chat button is absent when onQuickChat is not provided', () => {
    const props = makeProps({ onQuickChat: undefined });
    render(<MobileActionButtons {...props} />);
    expect(screen.queryByText('Chat')).toBeNull();
  });
});
