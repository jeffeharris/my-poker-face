"""Calibrate a drama-score -> speak-probability gate against REAL recorded hands.

Replays recent hand_history rows through score_hand() (the circuit-narrative
drama scorer) to find the actual score distribution, then sweeps candidate
gate curves to see what post-hand commentary speak-rate each would produce.

Baseline to beat: ~96% of hands have >=1 speaker, ~2.0 speakers/commented hand.

  docker compose exec -T backend python3 - < scripts/drama_gate_calibration.py
"""
import sqlite3, json, statistics as st

from poker.memory.hand_history import RecordedHand
from poker.memory.hand_score import score_hand

DB = "/app/data/poker_games.db"
N = 6000  # recent hands to replay

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

rows = c.execute(
    f"""SELECT * FROM hand_history ORDER BY id DESC LIMIT {N}"""
).fetchall()


def row_to_hand(r):
    d = {
        "game_id": r["game_id"],
        "hand_number": r["hand_number"],
        "timestamp": r["timestamp"],
        "players": json.loads(r["players_json"] or "[]"),
        "hole_cards": json.loads(r["hole_cards_json"] or "{}"),
        "community_cards": json.loads(r["community_cards_json"] or "[]"),
        "actions": json.loads(r["actions_json"] or "[]"),
        "winners": json.loads(r["winners_json"] or "[]"),
        "pot_size": r["pot_size"] or 0,
        "was_showdown": bool(r["showdown"]),
        "deck_seed": r["deck_seed"],
        "community_cards_by_phase": json.loads(r["community_cards_by_phase_json"] or "{}"),
    }
    return RecordedHand.from_dict(d)


# per-hand list of per-player drama scores
hand_scores = []  # list[list[int]]
all_scores = []
bad = 0
for r in rows:
    try:
        h = row_to_hand(r)
    except Exception:
        bad += 1
        continue
    ps = []
    for p in h.players:
        try:
            s = score_hand(h, p.name).score  # big_blind=None (conservative), equity=None
        except Exception:
            continue
        ps.append(s)
        all_scores.append(s)
    if ps:
        hand_scores.append(ps)


def pct(vals, p):
    vals = sorted(vals)
    if not vals:
        return 0
    k = (len(vals) - 1) * p / 100
    f = int(k)
    return round(vals[f] + (vals[min(f + 1, len(vals) - 1)] - vals[f]) * (k - f), 1)


print(f"\n==== DRAMA-SCORE CALIBRATION (real hands, n_hands={len(hand_scores)}, bad={bad}) ====")
print(f"per-(hand,player) drama score, big_blind=None/equity=None (conservative):")
print(f"  n={len(all_scores)} mean={st.mean(all_scores):.1f} "
      f"p25={pct(all_scores,25)} p50={pct(all_scores,50)} p75={pct(all_scores,75)} "
      f"p90={pct(all_scores,90)} p99={pct(all_scores,99)} max={max(all_scores)}")
# how many distinct hands have at least one >=K player
for K in (15, 20, 25, 30, 40, 50):
    f = sum(1 for ps in hand_scores if max(ps) >= K) / len(hand_scores)
    print(f"  hands with a player scoring >= {K:>2}: {100*f:.0f}%")


def clamp01(x):
    return max(0.0, min(1.0, x))


def expected(curve, chat=0.5):
    """Return (frac hands with >=1 expected speaker p>0.5, mean expected speakers/hand)."""
    p1, spk = [], []
    for ps in hand_scores:
        probs = [clamp01(curve(s, chat)) for s in ps]
        spk.append(sum(probs))
        # P(>=1 speaker) = 1 - prod(1-p)
        prod = 1.0
        for p in probs:
            prod *= (1 - p)
        p1.append(1 - prod)
    return st.mean(p1), st.mean(spk)


print("\n-- CANDIDATE CURVES (evaluated at chattiness=0.5) --")
print("   baseline now: ~96% hands have a speaker, ~2.0 speakers/hand\n")

curves = {
    "A linear  a=1.3,b=.4  prob=1.3*(s/100)+.4*(c-.5)": lambda s, c: 1.3 * (s / 100) + 0.4 * (c - 0.5),
    "B linear  a=1.6,b=.4": lambda s, c: 1.6 * (s / 100) + 0.4 * (c - 0.5),
    "C thresh  T=20,S=30  +chat": lambda s, c: (s - 20) / 30 + 0.3 * (c - 0.5),
    "D thresh  T=25,S=35  +chat": lambda s, c: (s - 25) / 35 + 0.3 * (c - 0.5),
    "E thresh  T=18,S=28": lambda s, c: (s - 18) / 28 + 0.35 * (c - 0.5),
}
for name, fn in curves.items():
    for chat in (0.3, 0.5, 0.7):
        f1, ms = expected(fn, chat)
        tag = f"  @chat={chat}: {100*f1:4.0f}% hands w/speaker, {ms:.2f} spk/hand"
        print((name if chat == 0.3 else " " * len(name)) + tag)
    print()
