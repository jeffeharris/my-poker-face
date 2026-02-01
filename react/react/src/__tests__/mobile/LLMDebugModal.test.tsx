import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LLMDebugModal } from '../../components/mobile/LLMDebugModal';
import type { LLMDebugInfo } from '../../types/player';

// Mock lucide-react
vi.mock('lucide-react', () => ({
  X: ({ size: _size, ...props }: { size?: number } & Record<string, unknown>) => (
    <span data-testid="x-icon" {...props}>X</span>
  ),
}));

function makeDebugInfo(overrides: Partial<LLMDebugInfo> = {}): LLMDebugInfo {
  return {
    provider: 'openai',
    model: 'gpt-4',
    total_calls: 42,
    avg_latency_ms: 234,
    avg_cost_per_call: 0.001,
    ...overrides,
  };
}

function makeProps(overrides: Partial<Parameters<typeof LLMDebugModal>[0]> = {}) {
  return {
    isOpen: true,
    onClose: vi.fn(),
    playerName: 'Batman',
    debugInfo: makeDebugInfo(),
    ...overrides,
  };
}

describe('VT-07: LLMDebugModal — stats rendering and CRT aesthetic', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('Case 1: With debug info', () => {
    it('renders SYS.DEBUG title', () => {
      const props = makeProps();
      render(<LLMDebugModal {...props} />);
      expect(screen.getByText('SYS.DEBUG')).toBeTruthy();
    });

    it('displays player name', () => {
      const props = makeProps();
      render(<LLMDebugModal {...props} />);
      expect(screen.getByText('PLAYER:')).toBeTruthy();
      expect(screen.getByText('Batman')).toBeTruthy();
    });

    it('displays vendor and model', () => {
      const props = makeProps();
      render(<LLMDebugModal {...props} />);
      expect(screen.getByText('VENDOR')).toBeTruthy();
      expect(screen.getByText('openai')).toBeTruthy();
      expect(screen.getByText('MODEL')).toBeTruthy();
      expect(screen.getByText('gpt-4')).toBeTruthy();
    });

    it('displays latency and cost', () => {
      const props = makeProps();
      render(<LLMDebugModal {...props} />);
      expect(screen.getByText('AVG LATENCY')).toBeTruthy();
      expect(screen.getByText('234ms')).toBeTruthy();
      expect(screen.getByText('AVG COST')).toBeTruthy();
      expect(screen.getByText('$0.0010')).toBeTruthy();
    });

    it('displays API call count with progress blocks', () => {
      const props = makeProps();
      const { container } = render(<LLMDebugModal {...props} />);
      expect(screen.getByText('42 API calls this game')).toBeTruthy();
      // Should render 20 blocks (capped at 20)
      const blocks = container.querySelectorAll('.progress-block');
      expect(blocks.length).toBe(20);
    });

    it('renders fewer progress blocks for low call count', () => {
      const props = makeProps({ debugInfo: makeDebugInfo({ total_calls: 5 }) });
      const { container } = render(<LLMDebugModal {...props} />);
      expect(screen.getByText('5 API calls this game')).toBeTruthy();
      const blocks = container.querySelectorAll('.progress-block');
      expect(blocks.length).toBe(5);
    });

    it('displays reasoning effort when present', () => {
      const props = makeProps({
        debugInfo: makeDebugInfo({ reasoning_effort: 'high' }),
      });
      render(<LLMDebugModal {...props} />);
      expect(screen.getByText('REASONING')).toBeTruthy();
      expect(screen.getByText('high')).toBeTruthy();
    });

    it('does not display reasoning effort when absent', () => {
      const props = makeProps();
      render(<LLMDebugModal {...props} />);
      expect(screen.queryByText('REASONING')).toBeNull();
    });
  });

  describe('Case 2: No debug info', () => {
    it('shows NO DATA AVAILABLE text', () => {
      const props = makeProps({ debugInfo: undefined });
      render(<LLMDebugModal {...props} />);
      expect(screen.getByText('NO DATA AVAILABLE')).toBeTruthy();
    });

    it('shows hint text', () => {
      const props = makeProps({ debugInfo: undefined });
      render(<LLMDebugModal {...props} />);
      expect(screen.getByText('Play some hands to see stats')).toBeTruthy();
    });

    it('does not render stats section', () => {
      const props = makeProps({ debugInfo: undefined });
      const { container } = render(<LLMDebugModal {...props} />);
      expect(container.querySelector('.llm-debug-stats')).toBeNull();
    });
  });

  describe('Case 3: Modal closed', () => {
    it('renders nothing when isOpen is false', () => {
      const props = makeProps({ isOpen: false });
      const { container } = render(<LLMDebugModal {...props} />);
      expect(container.querySelector('.llm-debug-modal-overlay')).toBeNull();
      expect(container.querySelector('.llm-debug-modal')).toBeNull();
    });
  });

  describe('Case 4: High cost highlight', () => {
    it('applies high-cost class when cost > $0.01', () => {
      const props = makeProps({
        debugInfo: makeDebugInfo({ avg_cost_per_call: 0.05 }),
      });
      const { container } = render(<LLMDebugModal {...props} />);
      const costEl = container.querySelector('.cost-value');
      expect(costEl).toBeTruthy();
      expect(costEl!.classList.contains('high-cost')).toBe(true);
    });

    it('does not apply high-cost class when cost <= $0.01', () => {
      const props = makeProps({
        debugInfo: makeDebugInfo({ avg_cost_per_call: 0.001 }),
      });
      const { container } = render(<LLMDebugModal {...props} />);
      const costEl = container.querySelector('.cost-value');
      expect(costEl).toBeTruthy();
      expect(costEl!.classList.contains('high-cost')).toBe(false);
    });
  });

  describe('Interactions', () => {
    it('calls onClose when overlay is clicked', () => {
      const props = makeProps();
      const { container } = render(<LLMDebugModal {...props} />);
      const overlay = container.querySelector('.llm-debug-modal-overlay');
      fireEvent.click(overlay!);
      expect(props.onClose).toHaveBeenCalled();
    });

    it('does not call onClose when modal content is clicked', () => {
      const props = makeProps();
      const { container } = render(<LLMDebugModal {...props} />);
      const modal = container.querySelector('.llm-debug-modal');
      fireEvent.click(modal!);
      expect(props.onClose).not.toHaveBeenCalled();
    });

    it('calls onClose when close button is clicked', () => {
      const props = makeProps();
      render(<LLMDebugModal {...props} />);
      const closeBtn = screen.getByLabelText('Close');
      fireEvent.click(closeBtn);
      expect(props.onClose).toHaveBeenCalled();
    });

    it('calls onClose when Escape key is pressed', () => {
      const props = makeProps();
      render(<LLMDebugModal {...props} />);
      fireEvent.keyDown(document, { key: 'Escape' });
      expect(props.onClose).toHaveBeenCalled();
    });

    it('has correct ARIA attributes', () => {
      const props = makeProps();
      render(<LLMDebugModal {...props} />);
      const dialog = screen.getByRole('dialog');
      expect(dialog).toBeTruthy();
      expect(dialog.getAttribute('aria-modal')).toBe('true');
      expect(dialog.getAttribute('aria-labelledby')).toBe('llm-debug-title');
    });
  });
});
