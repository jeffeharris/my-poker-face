import { useState, useRef, useMemo, useEffect, useCallback } from 'react';
import type { Player } from '../types/player';
import type { CardDealTransforms } from '../types';

interface UseCardAnimationProps {
  hand: Player['hand'] | undefined;
  isFolded: boolean;
}

interface UseCardAnimationReturn {
  displayCards: Player['hand'] | null;
  cardTransforms: CardDealTransforms;
  isDealing: boolean;
  isExiting: boolean;
  cardsNeat: boolean;
  setCardsNeat: (neat: boolean) => void;
  toggleCardsNeat: () => void;
  handleExitAnimationEnd: () => void;
}

// Neat transforms for straightened card position
const neatTransforms: CardDealTransforms = {
  card1: { rotation: 0, offsetY: 0, offsetX: 0, startRotation: 0 },
  card2: { rotation: 0, offsetY: 0, offsetX: 0, startRotation: 0 },
  gap: 12,
};

/**
 * Hook to manage card dealing and exit animations for the hero's hand.
 * Handles the complexity of:
 * - Tracking when cards change
 * - Exit animations before new cards deal
 * - Random transforms for natural "dealt" look
 * - Neat/messy toggle state
 * - Timer cleanup on unmount
 */
export function useCardAnimation({ hand, isFolded }: UseCardAnimationProps): UseCardAnimationReturn {
  // Track if cards are currently being dealt (for animation)
  const [isDealing, setIsDealing] = useState(false);
  const [isExiting, setIsExiting] = useState(false);
  const prevHandId = useRef<string | null>(null);

  // Display cards persist after fold so player can watch the action
  const [displayCards, setDisplayCards] = useState<Player['hand'] | null>(null);
  const [displayTransforms, setDisplayTransforms] = useState<CardDealTransforms | null>(null);

  // Store pending cards during exit animation
  const pendingCards = useRef<Player['hand'] | null>(null);
  const pendingTransforms = useRef<CardDealTransforms | null>(null);

  // Timer ref for cleanup
  const dealingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Track if cards are in "neat" (straightened) position
  const [cardsNeat, setCardsNeat] = useState(false);

  // Create stable card identifiers (only changes when actual cards change)
  const card1Id = hand?.[0] ? `${hand[0].rank}-${hand[0].suit}` : null;
  const card2Id = hand?.[1] ? `${hand[1].rank}-${hand[1].suit}` : null;
  // Combined ID to detect change in EITHER card
  const handId = card1Id && card2Id ? `${card1Id}|${card2Id}` : null;

  // Random card transforms for natural "dealt" look
  // Card 1: -3deg base +/-7deg range, Card 2: +3deg base +/-7deg range
  // Y offset: +/-8px, X offset: +/-3px, Gap: 10px base +/-10px range
  // Start rotation: tilted into slide direction (~12-18deg more negative)
  const randomTransforms = useMemo(() => {
    const card1Rotation = -3 + (Math.random() * 14 - 7);  // -10 to +4
    const card2Rotation = 3 + (Math.random() * 14 - 7);   // -4 to +10

    // Start rotations: tilted toward direction of travel (from left)
    // Cards sliding right naturally tilt counterclockwise (-) during motion
    const card1StartRotation = card1Rotation - 12 - (Math.random() * 6);  // ~12-18deg more tilted
    const card2StartRotation = card2Rotation - 12 - (Math.random() * 6);

    return {
      card1: {
        rotation: card1Rotation,
        startRotation: card1StartRotation,
        offsetY: Math.random() * 16 - 8,          // -8 to +8
        offsetX: Math.random() * 6 - 3,           // -3 to +3
      },
      card2: {
        rotation: card2Rotation,
        startRotation: card2StartRotation,
        offsetY: Math.random() * 16 - 8,          // -8 to +8
        offsetX: Math.random() * 6 - 3,           // -3 to +3
      },
      gap: 10 + (Math.random() * 20 - 10),        // 0 to 20
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card1Id, card2Id]);

  // Handle card transitions
  useEffect(() => {
    // New cards arriving
    if (handId && handId !== prevHandId.current) {
      // If we have cards showing, trigger exit animation first
      if (prevHandId.current && displayCards) {
        pendingCards.current = hand || null;
        pendingTransforms.current = randomTransforms;
        setIsExiting(true);
      } else {
        // No previous cards, deal immediately
        setDisplayCards(hand || null);
        setDisplayTransforms(randomTransforms);
        setCardsNeat(false);
        setIsDealing(true);
        if (dealingTimerRef.current) clearTimeout(dealingTimerRef.current);
        dealingTimerRef.current = setTimeout(() => setIsDealing(false), 700);
      }
      prevHandId.current = handId;
    }

    // Cards became null (fold or hand end) - keep displaying them
    if (!handId && prevHandId.current) {
      prevHandId.current = null;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handId]);

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (dealingTimerRef.current) clearTimeout(dealingTimerRef.current);
    };
  }, []);

  // Called when exit animation completes
  const handleExitAnimationEnd = useCallback(() => {
    setIsExiting(false);
    setDisplayCards(pendingCards.current);
    setDisplayTransforms(pendingTransforms.current);
    setCardsNeat(false);
    setIsDealing(true);
    if (dealingTimerRef.current) clearTimeout(dealingTimerRef.current);
    dealingTimerRef.current = setTimeout(() => setIsDealing(false), 700);
  }, []);

  const toggleCardsNeat = useCallback(() => {
    setCardsNeat(n => !n);
  }, []);

  // Use neat or random transforms based on state
  // displayTransforms persists after fold so cards stay in position
  const activeTransforms = displayTransforms || randomTransforms;
  const cardTransforms = cardsNeat ? neatTransforms : activeTransforms;

  return {
    displayCards,
    cardTransforms,
    isDealing,
    isExiting,
    cardsNeat,
    setCardsNeat,
    toggleCardsNeat,
    handleExitAnimationEnd,
  };
}
