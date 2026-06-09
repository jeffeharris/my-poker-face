"""Objective baseline of AI speech verbosity & frequency from prompt_captures.

Measures the two things the user cares about, WITHOUT judging beat COUNT:
  1. words-per-action-beat  (*shoves chips forward menacingly across the felt*  -> too long)
  2. sentences/words-per-speech-beat (a beat that is a paragraph -> too long)
  3. end-of-hand commentary frequency + length (emotional_reaction / strategic_reflection)

Run inside the backend container (has sqlite + the DB mount):
  docker compose exec -T backend python3 - < scripts/chat_verbosity_baseline.py
  ssh root@PROD "docker exec -i poker-backend-1 python3 -" < scripts/chat_verbosity_baseline.py
"""
import sqlite3, json, re, statistics as st

DB = "/app/data/poker_games.db"
DAYS = 30  # recent window

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row


def pct(vals, p):
    if not vals:
        return 0
    vals = sorted(vals)
    k = (len(vals) - 1) * p / 100
    f = int(k)
    if f + 1 < len(vals):
        return round(vals[f] + (vals[f + 1] - vals[f]) * (k - f), 1)
    return round(vals[f], 1)


def dist(name, vals):
    if not vals:
        print(f"  {name}: (no data)")
        return
    print(
        f"  {name}: n={len(vals)} mean={st.mean(vals):.1f} "
        f"p50={pct(vals,50)} p90={pct(vals,90)} p99={pct(vals,99)} max={max(vals)}"
    )


def words(s):
    return len(re.findall(r"\b[\w']+\b", s))


def sentences(s):
    # strip leading/trailing whitespace, count terminal punctuation groups
    parts = re.split(r"[.!?]+(?:\s|$)", s.strip())
    return max(1, len([p for p in parts if p.strip()]))


# ---- pull recent captures that carry speech ----
rows = c.execute(
    f"""SELECT ai_response, call_type FROM prompt_captures
        WHERE created_at >= datetime('now', '-{DAYS} days')
          AND ai_response LIKE '%dramatic_sequence%'
    """
).fetchall()

action_words, speech_words, speech_sents, beats_per = [], [], [], []
er_words, sr_words = [], []
n_resp = 0
n_with_speech_beat = 0
n_with_er = 0

for r in rows:
    try:
        d = json.loads(r["ai_response"])
    except Exception:
        continue
    if not isinstance(d, dict):
        continue
    n_resp += 1
    seq = d.get("dramatic_sequence") or []
    if isinstance(seq, list):
        beats_per.append(len(seq))
        had_speech = False
        for b in seq:
            if not isinstance(b, str) or not b.strip():
                continue
            txt = b.strip()
            if txt.startswith("*") and txt.endswith("*"):
                action_words.append(words(txt))
            else:
                had_speech = True
                speech_words.append(words(txt))
                speech_sents.append(sentences(txt))
        if had_speech:
            n_with_speech_beat += 1
    # end-of-hand commentary fields
    er = d.get("emotional_reaction")
    sr = d.get("strategic_reflection") or d.get("strategic_reaction")
    if isinstance(er, str) and er.strip():
        er_words.append(words(er))
        n_with_er += 1
    if isinstance(sr, str) and sr.strip():
        sr_words.append(words(sr))

print(f"\n==== AI SPEECH VERBOSITY BASELINE (last {DAYS} days, DB={DB}) ====")
print(f"responses with a dramatic_sequence: {n_resp}")
print(f"  ...of which had >=1 spoken (non-action) beat: {n_with_speech_beat} "
      f"({100*n_with_speech_beat/max(1,n_resp):.0f}%)")
print("\n-- BEAT STRUCTURE (count, NOT being tightened) --")
dist("beats per response", beats_per)
print("\n-- ACTION-BEAT VERBOSITY  (*...* gestures) --")
dist("words per action beat", action_words)
print("\n-- SPEECH-BEAT VERBOSITY  (spoken lines) --")
dist("words per speech beat", speech_words)
dist("sentences per speech beat", speech_sents)
print("\n-- END-OF-HAND COMMENTARY --")
print(f"  responses carrying emotional_reaction: {n_with_er}")
dist("emotional_reaction words", er_words)
dist("strategic_reflection words", sr_words)

# ---- frequency: how often does end-of-hand commentary fire per hand ----
print("\n-- END-OF-HAND FREQUENCY (hand_commentary table) --")
try:
    total_hands = c.execute(
        f"""SELECT COUNT(DISTINCT game_id||'#'||hand_number) FROM prompt_captures
            WHERE created_at >= datetime('now','-{DAYS} days') AND hand_number IS NOT NULL"""
    ).fetchone()[0]
    hc = c.execute(
        """SELECT name FROM sqlite_master WHERE type='table' AND name='hand_commentary'"""
    ).fetchone()
    if hc:
        cols = [r[1] for r in c.execute("PRAGMA table_info(hand_commentary)")]
        gcol = "game_id" if "game_id" in cols else None
        hcol = "hand_number" if "hand_number" in cols else None
        rows_hc = c.execute(
            f"""SELECT COUNT(*) total,
                       COUNT(DISTINCT {gcol}||'#'||{hcol}) hands_with
                FROM hand_commentary
                WHERE created_at >= datetime('now','-{DAYS} days')"""
            if (gcol and hcol)
            else "SELECT COUNT(*) total, NULL hands_with FROM hand_commentary"
        ).fetchone()
        print(f"  distinct hands (recent): {total_hands:,}")
        print(f"  hand_commentary rows: {rows_hc['total']:,}, "
              f"hands with commentary: {rows_hc['hands_with']}")
        if rows_hc["hands_with"] and total_hands:
            print(f"  => commentary fires on ~{100*rows_hc['hands_with']/total_hands:.0f}% of hands; "
                  f"~{rows_hc['total']/max(1,rows_hc['hands_with']):.1f} speakers/commented-hand")
except Exception as e:
    print("  (frequency calc failed:", e, ")")
