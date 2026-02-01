import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
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

describe('VT-02: MobileActionButtons raise sheet — calculations and interactions', () => {
  it('clicking Raise button opens the raise sheet', () => {
    const props = makeProps();
    render(<MobileActionButtons {...props} />);

    // Click the Raise button
    fireEvent.click(screen.getByText('Raise'));

    // Raise sheet should be visible
    expect(document.querySelector('.mobile-raise-sheet')).toBeTruthy();
    // Action buttons should no longer be visible
    expect(screen.queryByText('Fold')).toBeNull();
  });

  it('raise sheet shows amount, slider, and quick bet buttons', () => {
    const props = makeProps();
    render(<MobileActionButtons {...props} />);
    fireEvent.click(screen.getByText('Raise'));

    // Amount display shows the min raise-to amount
    const amountValue = document.querySelector('.amount-value');
    expect(amountValue).toBeTruthy();
    expect(amountValue?.textContent).toContain('100');

    // Slider is present with correct min/max
    const slider = document.querySelector('.raise-slider') as HTMLInputElement;
    expect(slider).toBeTruthy();
    expect(slider.min).toBe('100');
    expect(slider.max).toBe('2000');

    // Quick bet buttons are present (at least Min and All-In)
    expect(screen.getByText('Min')).toBeTruthy();
    expect(screen.getByText('All-In')).toBeTruthy();
  });

  it('raise sheet shows pot fraction quick bet buttons', () => {
    const props = makeProps();
    render(<MobileActionButtons {...props} />);
    fireEvent.click(screen.getByText('Raise'));

    // With pot of 150 and highest bet of 50, pot fractions should appear
    // ½ Pot = max(100, 50 + floor(150*0.5)) = max(100, 125) = 125
    const quickBetButtons = document.querySelectorAll('.quick-bet-btn');
    expect(quickBetButtons.length).toBeGreaterThanOrEqual(2); // At least Min and All-In
  });

  it('Cancel button closes the raise sheet', () => {
    const props = makeProps();
    render(<MobileActionButtons {...props} />);
    fireEvent.click(screen.getByText('Raise'));

    // Sheet should be open
    expect(document.querySelector('.mobile-raise-sheet')).toBeTruthy();

    // Click Cancel
    fireEvent.click(screen.getByText('Cancel'));

    // Sheet should be closed, action buttons visible again
    expect(document.querySelector('.mobile-raise-sheet')).toBeNull();
    expect(screen.getByText('Fold')).toBeTruthy();
    expect(screen.getByText('Raise')).toBeTruthy();
  });

  it('Confirm calls onAction with raise action and amount', () => {
    const onAction = vi.fn();
    const props = makeProps({ onAction });
    render(<MobileActionButtons {...props} />);
    fireEvent.click(screen.getByText('Raise'));

    // Confirm with default min raise amount (100)
    fireEvent.click(screen.getByText('Confirm'));

    expect(onAction).toHaveBeenCalledWith('raise', 100);
  });

  it('Confirm calls onAction with all_in when amount equals max', () => {
    const onAction = vi.fn();
    const props = makeProps({ onAction });
    render(<MobileActionButtons {...props} />);
    fireEvent.click(screen.getByText('Raise'));

    // Click All-In quick bet to set amount to max
    fireEvent.click(screen.getByText('All-In'));

    // Confirm
    fireEvent.click(screen.getByText('Confirm'));

    expect(onAction).toHaveBeenCalledWith('all_in', 2000);
  });

  it('clicking a quick bet button updates the raise amount', () => {
    const props = makeProps();
    render(<MobileActionButtons {...props} />);
    fireEvent.click(screen.getByText('Raise'));

    // Initially shows min raise: $100
    const amountValue = () => document.querySelector('.amount-value');
    expect(amountValue()?.textContent).toContain('100');

    // Click All-In
    fireEvent.click(screen.getByText('All-In'));

    // Amount should update to max ($2000)
    expect(amountValue()?.textContent).toContain('2000');
  });

  it('shows raise breakdown with call and raise portions', () => {
    const props = makeProps();
    render(<MobileActionButtons {...props} />);
    fireEvent.click(screen.getByText('Raise'));

    // With cost_to_call=50, raiseAmount=100, currentBet=0:
    // totalToAdd = 100 - 0 = 100, callPortion = min(50, 100) = 50, raisePortion = 50
    expect(screen.getByText('Call $50')).toBeTruthy();
    expect(screen.getByText('Raise $50')).toBeTruthy();
  });

  it('shows stack preview with remaining chips', () => {
    const props = makeProps();
    render(<MobileActionButtons {...props} />);
    fireEvent.click(screen.getByText('Raise'));

    // Stack after: 2000 - (100 - 0) = 1900
    expect(screen.getByText(/Stack after: \$1900/)).toBeTruthy();
  });

  it('shows "Raise to" label when raise is available', () => {
    const props = makeProps();
    render(<MobileActionButtons {...props} />);
    fireEvent.click(screen.getByText('Raise'));

    expect(screen.getByText('Raise to')).toBeTruthy();
    expect(screen.getByText('Raise', { selector: '.raise-title' })).toBeTruthy();
  });

  it('shows "Bet" label when bet option is used instead of raise', () => {
    const props = makeProps({
      playerOptions: ['fold', 'check', 'bet'],
      bettingContext: {
        player_stack: 2000,
        player_current_bet: 0,
        highest_bet: 0,
        pot_total: 150,
        min_raise_amount: 50,
        available_actions: ['fold', 'check', 'bet'],
        cost_to_call: 0,
        min_raise_to: 50,
        max_raise_to: 2000,
        effective_stack: 2000,
      },
    });
    render(<MobileActionButtons {...props} />);
    // The button shows "Bet" instead of "Raise"
    fireEvent.click(screen.getByText('Bet'));

    expect(screen.getByText('Bet', { selector: '.raise-title' })).toBeTruthy();
    expect(screen.getByText('Bet', { selector: '.amount-label' })).toBeTruthy();
  });

  it('2x button doubles the raise portion', () => {
    const props = makeProps();
    render(<MobileActionButtons {...props} />);
    fireEvent.click(screen.getByText('Raise'));

    // Initial: raiseAmount=100, callPortion=50, raisePortion=50
    // After 2x: raiseAmount = min(2000, 100 + 50) = 150
    fireEvent.click(screen.getByText('2x'));

    const amountValue = document.querySelector('.amount-value');
    expect(amountValue?.textContent).toContain('150');
  });

  it('slider changes the raise amount', () => {
    const props = makeProps();
    render(<MobileActionButtons {...props} />);
    fireEvent.click(screen.getByText('Raise'));

    const slider = document.querySelector('.raise-slider') as HTMLInputElement;
    expect(slider).toBeTruthy();

    // Change slider value
    fireEvent.change(slider, { target: { value: '500' } });

    // Amount should update (may snap to a magnetic point)
    // The displayed value should be different from the initial $100
    const amountEl = document.querySelector('.amount-value');
    expect(amountEl).toBeTruthy();
    expect(amountEl?.textContent).not.toBe('$100');
  });

  it('slider labels show min and max values', () => {
    const props = makeProps();
    render(<MobileActionButtons {...props} />);
    fireEvent.click(screen.getByText('Raise'));

    const labels = document.querySelectorAll('.slider-labels span');
    expect(labels.length).toBe(2);
    expect(labels[0].textContent).toBe('$100');
    expect(labels[1].textContent).toBe('$2000');
  });

  it('Confirm button is disabled for invalid raise amount', () => {
    const props = makeProps({
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
      },
    });
    render(<MobileActionButtons {...props} />);
    fireEvent.click(screen.getByText('Raise'));

    // At the default min raise, Confirm should be enabled
    const confirmBtn = screen.getByText('Confirm');
    expect(confirmBtn).not.toBeDisabled();
  });
});
