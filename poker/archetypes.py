"""
Canonical archetype classification for poker players.

Single source of truth for all archetype boundaries across the system.
Two classification scales:

1. **Personality anchors** (0-1 scale): Used for AI personality config.
   Maps baseline_looseness + baseline_aggression to play style profiles.

2. **Observed stats** (VPIP/AF): Used for opponent modeling.
   Maps observed VPIP + aggression factor to play style labels.

Both scales produce the same 4-quadrant archetype names:
    tight-aggressive (TAG), loose-aggressive (LAG),
    tight-passive (Rock/Nit), loose-passive (Fish/Station)
"""

# ── Personality Anchor Thresholds (0-1 scale) ──────────────────────────
# Used by: HybridAIController._get_option_profile(), range_guidance
#
# Three zones for looseness:
#   < ANCHOR_TIGHT  → tight (selective hand range)
#   > ANCHOR_LOOSE  → loose (wide hand range)
#   between         → balanced/default
ANCHOR_TIGHT = 0.45       # looseness below this = tight
ANCHOR_LOOSE = 0.65       # looseness above this = loose
ANCHOR_AGGRESSIVE = 0.50  # aggression at or above this = aggressive


def classify_from_anchors(looseness: float, aggression: float) -> str:
    """Classify player from personality anchors into a profile key.

    Returns one of: 'tight_passive', 'tight_aggressive', 'loose_passive',
    'loose_aggressive', or 'default' (balanced middle zone).
    """
    if looseness < ANCHOR_TIGHT:
        return 'tight_passive' if aggression < ANCHOR_AGGRESSIVE else 'tight_aggressive'
    elif looseness > ANCHOR_LOOSE:
        return 'loose_passive' if aggression < ANCHOR_AGGRESSIVE else 'loose_aggressive'
    return 'default'


def archetype_label_from_anchors(looseness: float, aggression: float) -> str:
    """Human-readable archetype label from personality anchors.

    Returns one of: 'TAG', 'LAG', 'Rock', 'Fish', or 'Balanced'.
    """
    if looseness < ANCHOR_TIGHT:
        return 'Rock' if aggression < ANCHOR_AGGRESSIVE else 'TAG'
    elif looseness > ANCHOR_LOOSE:
        return 'Fish' if aggression < ANCHOR_AGGRESSIVE else 'LAG'
    return 'Balanced'


# ── Observed Stats Thresholds (VPIP / Aggression Factor) ───────────────
# Used by: OpponentTendencies, coach_engine
#
# VPIP (Voluntarily Put $ In Pot): fraction of hands player enters
# AF (Aggression Factor): (bets + raises) / calls
VPIP_TIGHT = 0.30          # VPIP below this = tight player
VPIP_LOOSE = 0.50          # VPIP above this = loose player
VPIP_VERY_SELECTIVE = 0.20 # VPIP below this = very selective (nit territory)
AF_PASSIVE = 0.50          # AF below this = passive player
AF_AGGRESSIVE = 1.50       # AF above this = aggressive player
AF_VERY_AGGRESSIVE = 2.00  # AF above this = very aggressive (maniac territory)
