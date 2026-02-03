"""
Board texture analysis for poker coaching and range tracking.

Classifies community card textures to help the coach provide
context-aware guidance (e.g., "wet board favors draws" vs
"dry board favors made hands").
"""

from typing import Dict, List

# Ranks ordered from high to low
RANKS = ['A', 'K', 'Q', 'J', 'T', '9', '8', '7', '6', '5', '4', '3', '2']
BROADWAY_RANKS = {'A', 'K', 'Q', 'J', 'T'}


def _rank_index(rank: str) -> int:
    """Get index of rank (A=0, K=1, ..., 2=12)."""
    return RANKS.index(rank)


def _extract_rank(card: str) -> str:
    """Extract rank from card string like 'Ah' or 'Td'."""
    return card[0]


def _extract_suit(card: str) -> str:
    """Extract suit from card string like 'Ah' or 'Td'."""
    return card[1]


def analyze_board_texture(community_cards: List[str]) -> Dict:
    """Analyze the texture of the community cards.

    Args:
        community_cards: List of card strings like ['Ah', 'Kd', '7s']

    Returns:
        Dict with texture analysis:
        - num_cards: int (0, 3, 4, or 5)
        - paired: bool (board has a pair)
        - double_paired: bool (board has two pairs)
        - trips_on_board: bool (three of same rank)
        - monotone: bool (all same suit)
        - two_tone: bool (exactly 2 suits)
        - rainbow: bool (all different suits, 3+ on flop)
        - connected: bool (3+ cards within 4-rank window)
        - high_card_count: int (broadway cards T+ on board)
        - texture_category: str ("dry", "semi_wet", "wet", "very_wet")

    For pre-flop (no cards), returns {"num_cards": 0}.
    """
    if not community_cards:
        return {"num_cards": 0}

    num_cards = len(community_cards)
    if num_cards < 3:
        return {"num_cards": num_cards}

    # Extract ranks and suits
    ranks = [_extract_rank(c) for c in community_cards]
    suits = [_extract_suit(c) for c in community_cards]

    # Count rank occurrences
    rank_counts = {}
    for r in ranks:
        rank_counts[r] = rank_counts.get(r, 0) + 1

    # Count suit occurrences
    suit_counts = {}
    for s in suits:
        suit_counts[s] = suit_counts.get(s, 0) + 1

    # Pairing analysis
    pair_count = sum(1 for count in rank_counts.values() if count == 2)
    trips_on_board = any(count >= 3 for count in rank_counts.values())
    paired = pair_count >= 1 or trips_on_board
    double_paired = pair_count >= 2

    # Suit analysis
    unique_suits = len(suit_counts)
    monotone = unique_suits == 1
    two_tone = unique_suits == 2
    rainbow = unique_suits >= 3  # 3 different suits on flop (or more)

    # Connectedness: check if 3+ cards within a 4-rank window
    rank_indices = sorted([_rank_index(r) for r in ranks])
    connected = _is_connected(rank_indices)

    # High cards (broadway: T, J, Q, K, A)
    high_card_count = sum(1 for r in ranks if r in BROADWAY_RANKS)

    # Calculate wetness score
    wetness = 0
    if monotone:
        wetness += 3
    elif two_tone:
        wetness += 1
    if connected:
        wetness += 2
    if paired:
        wetness += 1
    if high_card_count >= 2:
        wetness += 1

    # Determine texture category
    if wetness == 0:
        texture_category = "dry"
    elif wetness <= 2:
        texture_category = "semi_wet"
    elif wetness <= 4:
        texture_category = "wet"
    else:
        texture_category = "very_wet"

    return {
        "num_cards": num_cards,
        "paired": paired,
        "double_paired": double_paired,
        "trips_on_board": trips_on_board,
        "monotone": monotone,
        "two_tone": two_tone,
        "rainbow": rainbow,
        "connected": connected,
        "high_card_count": high_card_count,
        "texture_category": texture_category,
    }


def _is_connected(rank_indices: List[int]) -> bool:
    """Check if 3+ cards are within a 4-rank window.

    This indicates potential straight draw connectivity.
    A wheel (A-2-3-4-5) is also considered connected.

    Args:
        rank_indices: Sorted list of rank indices (A=0, K=1, ..., 2=12)

    Returns:
        True if the board is connected
    """
    if len(rank_indices) < 3:
        return False

    # Standard connectivity check: any 3 consecutive cards within 4-rank span
    for i in range(len(rank_indices) - 2):
        window = rank_indices[i:i + 3]
        if window[-1] - window[0] <= 4:
            return True

    # Special case: wheel connectivity (A, 2, 3, 4, 5)
    # Ace is index 0, 2 is index 12, 3 is 11, 4 is 10, 5 is 9
    has_ace = 0 in rank_indices
    wheel_ranks = {9, 10, 11, 12}  # 5, 4, 3, 2
    wheel_count = sum(1 for idx in rank_indices if idx in wheel_ranks)

    if has_ace and wheel_count >= 2:
        return True

    return False


def get_texture_description(texture: Dict) -> str:
    """Generate a human-readable description of board texture.

    Args:
        texture: Dict from analyze_board_texture()

    Returns:
        String description like "wet, monotone flop with 2 high cards"
    """
    if texture.get("num_cards", 0) == 0:
        return "pre-flop"

    parts = []

    # Texture category
    category = texture.get("texture_category", "unknown")
    parts.append(category)

    # Key features
    features = []
    if texture.get("monotone"):
        features.append("monotone")
    elif texture.get("two_tone"):
        features.append("two-tone")
    elif texture.get("rainbow"):
        features.append("rainbow")

    if texture.get("trips_on_board"):
        features.append("trips on board")
    elif texture.get("double_paired"):
        features.append("double-paired")
    elif texture.get("paired"):
        features.append("paired")

    if texture.get("connected"):
        features.append("connected")

    if features:
        parts.append(", ".join(features))

    # Street name
    num_cards = texture.get("num_cards", 0)
    if num_cards == 3:
        street = "flop"
    elif num_cards == 4:
        street = "turn"
    elif num_cards == 5:
        street = "river"
    else:
        street = "board"

    parts.append(street)

    # High card count
    high_cards = texture.get("high_card_count", 0)
    if high_cards >= 2:
        parts.append(f"with {high_cards} high cards")

    return " ".join(parts)
