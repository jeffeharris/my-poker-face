/**
 * Shared hook for betting calculations.
 *
 * Centralizes all betting math that was previously duplicated between
 * desktop ActionButtons and mobile MobileActionButtons components.
 *
 * Uses "raise TO" semantics throughout - all amounts represent total bet amounts.
 */

import { useMemo } from 'react';

/**
 * BettingContext from the backend API.
 * All amounts are absolute chip values.
 */
export interface BettingContext {
  player_stack: number;
  player_current_bet: number;
  highest_bet: number;
  pot_total: number;
  min_raise_amount: number;
  available_actions: string[];
  // Computed properties from backend
  cost_to_call: number;
  min_raise_to: number;
  max_raise_to: number;
  effective_stack: number;
}

/**
 * Pot fraction quick bet amounts.
 */
export interface PotFractions {
  quarter: number;
  third: number;
  half: number;
  twoThirds: number;
  threeQuarters: number;
  full: number;
}

/**
 * Breakdown of a raise amount into its components.
 */
export interface RaiseBreakdown {
  callPortion: number;
  raisePortion: number;
  totalToAdd: number;
  stackAfter: number;
}

/**
 * Quick bet button configuration.
 */
export interface QuickBet {
  label: string;
  amount: number;
  id: string;
  alwaysShow?: boolean;
}

/**
 * Return type of the useBettingCalculations hook.
 */
export interface BettingCalculations {
  // Safe values (guaranteed valid numbers)
  safeMinRaise: number;
  safeMinRaiseTo: number;
  safeMaxRaiseTo: number;
  safePotSize: number;
  safeHighestBet: number;
  safeCurrentBet: number;
  safeStack: number;
  callAmount: number;

  // Pot fraction amounts (all as "raise TO" amounts)
  potFractions: PotFractions;

  // Slider snap increment (0.5BB based)
  snapIncrement: number;

  // Magnetic snap points (pot fractions within valid range)
  magneticSnapPoints: number[];

  // Quick bet buttons (filtered by affordability)
  quickBets: QuickBet[];

  // Helper functions
  roundToSnap: (value: number) => number;
  snapWithMagnets: (value: number) => number;
  isValidRaise: (amount: number) => boolean;
  getBreakdown: (raiseToAmount: number) => RaiseBreakdown;
  getDefaultRaise: () => number;
}

/**
 * Hook that provides all betting calculations.
 *
 * @param context - BettingContext from the backend (or constructed locally)
 * @param bigBlind - Big blind amount for snap increment calculation
 * @returns Memoized betting calculations
 */
export function useBettingCalculations(
  context: BettingContext | null,
  bigBlind: number
): BettingCalculations {
  return useMemo(() => {
    // Provide safe defaults if context is null
    const safeContext = context ?? {
      player_stack: 0,
      player_current_bet: 0,
      highest_bet: 0,
      pot_total: 0,
      min_raise_amount: bigBlind,
      available_actions: [],
      cost_to_call: 0,
      min_raise_to: bigBlind,
      max_raise_to: 0,
      effective_stack: 0,
    };

    // Ensure all values are valid numbers
    const safeMinRaise = Math.max(1, safeContext.min_raise_amount || bigBlind);
    const safePotSize = Math.max(0, safeContext.pot_total || 0);
    const safeHighestBet = Math.max(0, safeContext.highest_bet || 0);
    const safeCurrentBet = Math.max(0, safeContext.player_current_bet || 0);
    const safeStack = Math.max(0, safeContext.player_stack || 0);
    const callAmount = Math.max(0, safeContext.cost_to_call || 0);

    // Min/max raise TO amounts
    const safeMinRaiseTo = Math.max(safeHighestBet + safeMinRaise, safeContext.min_raise_to || 0);
    const safeMaxRaiseTo = Math.max(0, safeContext.max_raise_to || (safeCurrentBet + safeStack));

    // Calculate pot fraction amounts (as "raise TO" amounts)
    const potFractions: PotFractions = {
      quarter: Math.max(safeMinRaiseTo, Math.floor(safePotSize / 4)),
      third: Math.max(safeMinRaiseTo, Math.floor(safePotSize / 3)),
      half: Math.max(safeMinRaiseTo, Math.floor(safePotSize / 2)),
      twoThirds: Math.max(safeMinRaiseTo, Math.floor(safePotSize * 0.67)),
      threeQuarters: Math.max(safeMinRaiseTo, Math.floor(safePotSize * 0.75)),
      full: Math.max(safeMinRaiseTo, safePotSize),
    };

    // Calculate snap increment: 0.5BB rounded to nearest 5
    // BB=75 → 37.5 → 40, BB=100 → 50, BB=20 → 10
    const halfBB = bigBlind / 2;
    const snapIncrement = Math.max(5, Math.round(halfBB / 5) * 5);

    // Round to nearest snap increment
    const roundToSnap = (value: number): number => {
      return Math.round(value / snapIncrement) * snapIncrement;
    };

    // Magnetic snap points - pot fractions that the slider "sticks" to
    const magneticSnapPoints = [
      potFractions.quarter,
      potFractions.third,
      potFractions.half,
      potFractions.twoThirds,
      potFractions.threeQuarters,
      potFractions.full,
    ].filter(v => v >= safeMinRaiseTo && v <= safeMaxRaiseTo);

    // Snap with magnetic attraction to pot fractions
    const snapWithMagnets = (value: number): number => {
      // Check if we're close to a magnetic snap point (within 1BB)
      const magnetThreshold = bigBlind;
      for (const snapPoint of magneticSnapPoints) {
        if (Math.abs(value - snapPoint) <= magnetThreshold) {
          return snapPoint;
        }
      }
      // Otherwise, round to normal snap increment
      return roundToSnap(value);
    };

    // Check if a raise TO amount is valid
    const isValidRaise = (raiseToAmount: number): boolean => {
      // All-in is always valid
      if (raiseToAmount === safeMaxRaiseTo) return true;
      // Must be within range
      return raiseToAmount >= safeMinRaiseTo && raiseToAmount <= safeMaxRaiseTo;
    };

    // Get breakdown of a raise TO amount
    const getBreakdown = (raiseToAmount: number): RaiseBreakdown => {
      const totalToAdd = raiseToAmount - safeCurrentBet;
      const callPortion = Math.min(callAmount, totalToAdd);
      const raisePortion = Math.max(0, totalToAdd - callPortion);
      const stackAfter = Math.max(0, safeStack - totalToAdd);

      return {
        callPortion,
        raisePortion,
        totalToAdd,
        stackAfter,
      };
    };

    // Get default raise amount (for initial slider position)
    const getDefaultRaise = (): number => {
      // Default to highest bet + 2x big blind, snapped
      const defaultAmount = safeHighestBet + Math.max(safeMinRaise, bigBlind * 2);
      return roundToSnap(Math.min(defaultAmount, safeMaxRaiseTo));
    };

    // Build quick bet buttons (filtered by what player can afford)
    const quickBets: QuickBet[] = [
      { label: 'Min', amount: safeMinRaiseTo, id: 'min', alwaysShow: true },
      { label: '¼ Pot', amount: potFractions.quarter, id: '1/4' },
      { label: '⅓ Pot', amount: potFractions.third, id: '1/3' },
      { label: '½ Pot', amount: potFractions.half, id: '1/2' },
      { label: '⅔ Pot', amount: potFractions.twoThirds, id: '2/3' },
      { label: '¾ Pot', amount: potFractions.threeQuarters, id: '3/4' },
      { label: 'Pot', amount: potFractions.full, id: 'pot' },
      { label: 'All-In', amount: safeMaxRaiseTo, id: 'all-in', alwaysShow: true },
    ].filter(bet =>
      bet.amount <= safeMaxRaiseTo &&
      (bet.alwaysShow || bet.amount > safeMinRaiseTo)
    );

    return {
      safeMinRaise,
      safeMinRaiseTo,
      safeMaxRaiseTo,
      safePotSize,
      safeHighestBet,
      safeCurrentBet,
      safeStack,
      callAmount,
      potFractions,
      snapIncrement,
      magneticSnapPoints,
      quickBets,
      roundToSnap,
      snapWithMagnets,
      isValidRaise,
      getBreakdown,
      getDefaultRaise,
    };
  }, [context, bigBlind]);
}

/**
 * Create a BettingContext from individual props (for backward compatibility).
 * Use this when the backend hasn't been updated to send betting_context yet.
 */
export function createBettingContext(props: {
  playerStack: number;
  playerCurrentBet: number;
  highestBet: number;
  potSize: number;
  minRaise: number;
  playerOptions: string[];
}): BettingContext {
  const costToCall = Math.max(0, props.highestBet - props.playerCurrentBet);
  const minRaiseTo = props.highestBet + props.minRaise;
  const maxRaiseTo = props.playerCurrentBet + props.playerStack;
  const effectiveStack = Math.max(0, props.playerStack - costToCall);

  return {
    player_stack: props.playerStack,
    player_current_bet: props.playerCurrentBet,
    highest_bet: props.highestBet,
    pot_total: props.potSize,
    min_raise_amount: props.minRaise,
    available_actions: props.playerOptions,
    cost_to_call: costToCall,
    min_raise_to: minRaiseTo,
    max_raise_to: maxRaiseTo,
    effective_stack: effectiveStack,
  };
}
