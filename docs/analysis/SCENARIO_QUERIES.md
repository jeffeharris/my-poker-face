# AI Decision Quality - Scenario Detection Queries

Quick reference for detecting each problematic scenario.

---

## Scenario Labels

| Label | Description | Severity |
|-------|-------------|----------|
| `FOLD_MISTAKE` | Folding hands with positive EV | HIGH |
| `RAISE_WAR` | 3+ raises in single betting round | MEDIUM |
| `EXTREME_RAISE_WAR` | 7+ raises in single betting round | HIGH |
| `BAD_ALL_IN` | All-in with very low equity | MEDIUM |
| `SHORT_STACK_FOLD` | Folding with < 2 BB | HIGH |
| `POT_COMMITTED_FOLD` | Folding after investing > remaining stack | HIGH |
| `BLUFF_OVERESTIMATE` | All-in bluff on river vs committed opponents | MEDIUM |

---

## Python Detection Script

```python
import sqlite3
import json
import re
from collections import defaultdict

def analyze_decisions(db_path='data/poker_games.db'):
    """Run all scenario detection queries and return summary."""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    results = {}

    # 1. FOLD_MISTAKE
    cursor.execute("""
        SELECT COUNT(*), SUM(ev_lost)
        FROM player_decision_analysis
        WHERE action_taken = 'fold' AND decision_quality = 'mistake'
    """)
    row = cursor.fetchone()
    results['FOLD_MISTAKE'] = {'count': row[0], 'ev_lost': row[1]}

    # 2. BAD_ALL_IN
    cursor.execute("""
        SELECT COUNT(*), SUM(ev_lost)
        FROM player_decision_analysis
        WHERE action_taken = 'all_in' AND decision_quality = 'mistake'
    """)
    row = cursor.fetchone()
    results['BAD_ALL_IN'] = {'count': row[0], 'ev_lost': row[1]}

    # 3. RAISE_WAR (requires Python processing)
    cursor.execute("""
        SELECT game_id, hand_number, phase, action_taken
        FROM prompt_captures
        WHERE action_taken IS NOT NULL
        ORDER BY game_id, hand_number, phase, created_at
    """)

    rounds = defaultdict(list)
    for row in cursor.fetchall():
        game_id, hand_num, phase, action = row
        key = (game_id, hand_num, phase)
        rounds[key].append(action)

    raise_wars = 0
    extreme_wars = 0
    for key, actions in rounds.items():
        raises = sum(1 for a in actions if a in ('raise', 'all_in'))
        if raises >= 3:
            raise_wars += 1
        if raises >= 7:
            extreme_wars += 1

    results['RAISE_WAR'] = {'count': raise_wars}
    results['EXTREME_RAISE_WAR'] = {'count': extreme_wars}

    # 4. SHORT_STACK_FOLD and POT_COMMITTED_FOLD
    cursor.execute("""
        SELECT player_stack, user_message, action_taken
        FROM prompt_captures
        WHERE action_taken = 'fold' AND player_stack > 0
    """)

    short_stack_folds = 0
    pot_committed_folds = 0

    for row in cursor.fetchall():
        stack, user_msg, action = row
        if not user_msg:
            continue

        # Extract BB
        bb = 250
        match = re.search(r'Blinds:\s*\$?(\d+)/\$?(\d+)', user_msg)
        if match:
            bb = int(match.group(2))

        stack_bb = stack / bb if bb > 0 else 0

        # Extract already_bet
        match = re.search(r"How much you've bet:\s*\$?(\d+)", user_msg)
        already_bet = int(match.group(1)) if match else 0

        if stack_bb < 2:
            short_stack_folds += 1

        if already_bet > stack:
            pot_committed_folds += 1

    results['SHORT_STACK_FOLD'] = {'count': short_stack_folds}
    results['POT_COMMITTED_FOLD'] = {'count': pot_committed_folds}

    conn.close()
    return results


def get_detailed_fold_mistakes(db_path='data/poker_games.db', limit=20):
    """Get detailed info on worst fold mistakes."""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            player_name,
            phase,
            equity,
            ev_lost,
            pot_total,
            cost_to_call,
            player_hand,
            community_cards,
            optimal_action
        FROM player_decision_analysis
        WHERE action_taken = 'fold' AND decision_quality = 'mistake'
        ORDER BY ev_lost DESC
        LIMIT ?
    """, (limit,))

    results = []
    for row in cursor.fetchall():
        results.append({
            'player': row[0],
            'phase': row[1],
            'equity': row[2],
            'ev_lost': row[3],
            'pot': row[4],
            'cost': row[5],
            'hand': json.loads(row[6]) if row[6] else [],
            'board': json.loads(row[7]) if row[7] else [],
            'optimal': row[8]
        })

    conn.close()
    return results


def get_raise_war_details(db_path='data/poker_games.db', min_raises=7):
    """Get details on extreme raise wars."""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT game_id, hand_number, phase, player_name, action_taken,
               pot_total, raise_amount
        FROM prompt_captures
        WHERE action_taken IS NOT NULL
        ORDER BY game_id, hand_number, phase, created_at
    """)

    rounds = defaultdict(list)
    for row in cursor.fetchall():
        game_id, hand_num, phase, player, action, pot, raise_amt = row
        key = (game_id, hand_num, phase)
        rounds[key].append({
            'player': player,
            'action': action,
            'pot': pot,
            'raise_amount': raise_amt
        })

    wars = []
    for key, actions in rounds.items():
        raises = [a for a in actions if a['action'] in ('raise', 'all_in')]
        if len(raises) >= min_raises:
            wars.append({
                'game_id': key[0],
                'hand': key[1],
                'phase': key[2],
                'num_raises': len(raises),
                'raisers': [r['player'] for r in raises],
                'final_pot': raises[-1]['pot'] if raises else 0
            })

    conn.close()
    return sorted(wars, key=lambda x: -x['num_raises'])


def get_bad_allins_with_reasoning(db_path='data/poker_games.db', limit=10):
    """Get bad all-ins with AI reasoning from inner_monologue."""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            d.player_name,
            d.phase,
            d.equity,
            d.ev_lost,
            d.player_hand,
            d.community_cards,
            p.ai_response,
            p.model
        FROM player_decision_analysis d
        JOIN prompt_captures p
            ON d.game_id = p.game_id
            AND d.hand_number = p.hand_number
            AND d.player_name = p.player_name
            AND d.phase = p.phase
        WHERE d.action_taken = 'all_in' AND d.decision_quality = 'mistake'
        ORDER BY d.ev_lost DESC
        LIMIT ?
    """, (limit,))

    results = []
    for row in cursor.fetchall():
        player, phase, eq, ev_lost, hand_json, board_json, response, model = row

        # Extract inner_monologue
        reasoning = ""
        try:
            resp = json.loads(response)
            reasoning = resp.get('inner_monologue', '')
        except:
            pass

        results.append({
            'player': player,
            'phase': phase,
            'equity': eq,
            'ev_lost': ev_lost,
            'hand': json.loads(hand_json) if hand_json else [],
            'board': json.loads(board_json) if board_json else [],
            'model': model,
            'reasoning': reasoning
        })

    conn.close()
    return results


def get_pot_committed_folds(db_path='data/poker_games.db', limit=20):
    """Get folds where player invested more than remaining stack."""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            p.player_name,
            p.player_stack,
            p.pot_total,
            p.cost_to_call,
            p.user_message,
            p.ai_response,
            p.model,
            d.equity,
            d.ev_lost
        FROM prompt_captures p
        LEFT JOIN player_decision_analysis d
            ON p.game_id = d.game_id
            AND p.hand_number = d.hand_number
            AND p.player_name = d.player_name
            AND p.phase = d.phase
        WHERE p.action_taken = 'fold' AND p.player_stack > 0
    """)

    results = []
    for row in cursor.fetchall():
        (player, stack, pot, cost, user_msg, response, model, equity, ev_lost) = row

        if not user_msg:
            continue

        # Extract already_bet
        match = re.search(r"How much you've bet:\s*\$?(\d+)", user_msg)
        already_bet = int(match.group(1)) if match else 0

        if already_bet > stack:
            ratio = already_bet / stack if stack > 0 else float('inf')
            pot_odds = pot / cost if cost > 0 else float('inf')

            results.append({
                'player': player,
                'already_bet': already_bet,
                'stack': stack,
                'ratio': ratio,
                'pot': pot,
                'cost': cost,
                'pot_odds': pot_odds,
                'equity': equity,
                'ev_lost': ev_lost,
                'model': model
            })

    # Sort by ratio (most absurd first)
    results.sort(key=lambda x: -x['ratio'])
    return results[:limit]


# Example usage
if __name__ == '__main__':
    print("=== Decision Quality Summary ===")
    summary = analyze_decisions()
    for scenario, data in summary.items():
        print(f"{scenario}: {data}")

    print("\n=== Top 5 Worst Fold Mistakes ===")
    for fm in get_detailed_fold_mistakes(limit=5):
        print(f"{fm['player']}: {fm['hand']} - {fm['ev_lost']} EV lost")

    print("\n=== Extreme Raise Wars ===")
    for war in get_raise_war_details(min_raises=10)[:3]:
        print(f"Game {war['game_id']}, Hand {war['hand']}: {war['num_raises']} raises")
```

---

## SQL Quick Reference

### Check Current Error Rates
```sql
-- Overall mistake rate by action
SELECT
    action_taken,
    COUNT(*) as total,
    SUM(CASE WHEN decision_quality = 'mistake' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as mistake_pct
FROM player_decision_analysis
GROUP BY action_taken;
```

### Find Worst Model
```sql
SELECT
    p.model,
    SUM(CASE WHEN d.decision_quality = 'mistake' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as mistake_pct,
    SUM(d.ev_lost) as total_ev_lost
FROM prompt_captures p
JOIN player_decision_analysis d
    ON p.game_id = d.game_id
    AND p.hand_number = d.hand_number
    AND p.player_name = d.player_name
GROUP BY p.model
HAVING COUNT(*) >= 100
ORDER BY mistake_pct DESC;
```

### Find Worst Personality
```sql
SELECT
    player_name,
    SUM(CASE WHEN decision_quality = 'mistake' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as mistake_pct,
    SUM(ev_lost) as total_ev_lost
FROM player_decision_analysis
GROUP BY player_name
HAVING COUNT(*) >= 50
ORDER BY mistake_pct DESC
LIMIT 10;
```

### Recent Mistakes (for monitoring)
```sql
SELECT
    d.created_at,
    d.player_name,
    d.action_taken,
    d.optimal_action,
    d.equity,
    d.ev_lost,
    p.model
FROM player_decision_analysis d
JOIN prompt_captures p
    ON d.game_id = p.game_id
    AND d.hand_number = p.hand_number
    AND d.player_name = p.player_name
WHERE d.decision_quality = 'mistake'
ORDER BY d.created_at DESC
LIMIT 20;
```

---

## Thresholds for Alerts

| Scenario | Alert Threshold | Current Value |
|----------|-----------------|---------------|
| Fold mistake rate | > 50% | 44.1% |
| Raise war rate | > 10% | 7.8% |
| Bad all-in rate | > 15% | 11.4% |
| Short stack fold rate | > 70% | 62.7% |
| Pot-committed fold count | > 100/session | varies |

---

## Integration Points

### Add to Experiment Runner
```python
from docs.analysis.SCENARIO_QUERIES import analyze_decisions

# After tournament completes
results = analyze_decisions()
if results['FOLD_MISTAKE']['count'] > threshold:
    log.warning(f"High fold mistakes: {results['FOLD_MISTAKE']}")
```

### Add to Dashboard
- Display current error rates by model
- Show trend over time
- Alert on threshold breach
