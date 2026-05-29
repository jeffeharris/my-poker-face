"""Training / Coaching game mode.

A non-counting practice mode: the player spars against selectable-difficulty
opponents with the coach auto-engaged. Games here do NOT write to the cash
economy, prestige, AI relationship memory, or leaderboards — only the per-user
coach skill-progression record. See docs/plans/TRAINING_MODE.md.

This package holds the training-specific domain logic (opponent rosters now;
scenario model + state builder in later phases). The Flask surface lives in
flask_app/routes/training_routes.py.
"""
