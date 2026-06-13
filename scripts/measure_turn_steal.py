"""Read-only measurement of the turn float-and-steal spot from historical decision data.

Identifies the give-up line — a villain c-bets the flop, the hero floats IP, the villain
checks the turn — and reports how often the hero (air/weak) steals vs checks back, plus
the villain's fold equity when the hero does bet. Pure SELECTs; writes nothing.

Run locally:  docker compose exec -T backend python3 /app/scripts/measure_turn_steal.py
Run on prod:  cat scripts/measure_turn_steal.py | ssh root@<host> \
                  "docker exec -i poker-backend-1 python3 - "
"""

import json
import os
import sqlite3
from collections import Counter

DB = os.environ.get("POKER_DB", "/app/data/poker_games.db")
AGG = {"bet", "raise", "all_in"}

c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

# Index hand_history by (game_id, hand_number) → action list.
hh = {}
for gid, hn, aj in c.execute("SELECT game_id, hand_number, actions_json FROM hand_history"):
    try:
        hh[(gid, hn)] = json.loads(aj)
    except Exception:
        pass

rows = c.execute(
    "SELECT game_id, hand_number, player_name, strategy_pipeline_snapshot_json "
    "FROM player_decision_analysis "
    "WHERE phase='TURN' AND strategy_pipeline_snapshot_json LIKE '%node_key%'"
).fetchall()

# Broad signal: turn | IP | unopened (checked to hero), by made_tier.
broad = {}
# Give-up line: villain c-bet flop, hero floated IP, villain checked turn, hero air/weak.
giveup = Counter()
hero_act = Counter()
foldeq = Counter()

for gid, hn, pname, snap in rows:
    try:
        s = json.loads(snap)
    except Exception:
        continue
    nk = (s.get("node_key") or "").split("|")
    if len(nk) != 8:
        continue
    street, pos, _pot, _tex, made, _draw, facing, _spr = nk
    if street != "turn" or pos != "IP" or facing != "unopened":
        continue
    resolved = s.get("resolved_action", "?")
    aggr = resolved in AGG
    b = broad.setdefault(made, [0, 0])
    b[0] += 1
    b[1] += 1 if aggr else 0

    if made not in ("air", "weak_made"):
        continue
    acts = hh.get((gid, hn))
    if not acts:
        continue
    flop = [a for a in acts if a["phase"] == "FLOP"]
    turn = [a for a in acts if a["phase"] == "TURN"]
    cbettor = next(
        (a["player_name"] for a in flop if a["action"] in AGG and a["player_name"] != pname),
        None,
    )
    if cbettor is None:
        continue  # no villain c-bet → not a give-up line
    if not any(a["player_name"] == pname and a["action"] == "call" for a in flop):
        continue  # hero didn't float the flop
    cb_turn = [a for a in turn if a["player_name"] == cbettor]
    if not cb_turn or cb_turn[0]["action"] != "check":
        continue  # villain didn't give up the turn
    giveup["spots"] += 1
    ha = [a for a in turn if a["player_name"] == pname]
    if not ha:
        continue
    act = ha[0]["action"]
    hero_act[act] += 1
    if act in AGG:
        after = turn[turn.index(ha[0]) + 1:]
        vresp = [a for a in after if a["player_name"] == cbettor]
        if vresp:
            foldeq["villain_folded" if vresp[0]["action"] == "fold" else "villain_continued"] += 1
        else:
            foldeq["villain_no_further_action"] += 1


def pct(n, d):
    return f"{100.0 * n / d:.0f}%" if d else "n/a"


print(f"DB: {DB}")
print(f"hand_history hands: {len(hh)}   turn decisions w/ snapshot: {len(rows)}")
print("\n[broad] TURN | IP | checked-to-hero — bet% by hand_strength:")
for made, (n, b) in sorted(broad.items(), key=lambda x: -x[1][0]):
    print(f"  {made:14s} n={n:<5d} bet={pct(b, n)}")

tot = sum(hero_act.values())
steal = sum(v for k, v in hero_act.items() if k in AGG)
print(f"\n[give-up line] villain c-bet flop, hero floated IP, villain checked turn, air/weak hero:")
print(f"  spots: {giveup['spots']}   hero action: {dict(hero_act.most_common())}")
print(f"  hero STEAL: {steal} = {pct(steal, tot)}   hero CHECK (missed): "
      f"{hero_act.get('check', 0)} = {pct(hero_act.get('check', 0), tot)}")
fe_f = foldeq.get("villain_folded", 0)
fe_tot = fe_f + foldeq.get("villain_continued", 0)
print(f"  fold equity when hero bet: {dict(foldeq)}  → villain fold-to-steal = {pct(fe_f, fe_tot)}")
