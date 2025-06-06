"""
Fix for pressure stats not tracking wins properly.

The issue: Big wins are detected but not showing in leaderboards because
the pot threshold calculation might be excluding valid wins.
"""

# The current logic in pressure_detector.py:
# is_big_pot = pot_total > avg_stack * 1.5

# This might be too restrictive. Let's adjust the threshold and ensure
# we're tracking all wins, not just "big" wins for the leaderboard.

# Changes needed:
# 1. Lower the big pot threshold
# 2. Track ALL wins in stats, not just big wins
# 3. Ensure pot size is correctly passed to stats tracker