#!/usr/bin/env python3
"""Score 2AFC perceptibility results (backlog #12, Phase 5).

The SCORING half of the 2AFC perceptibility harness. Two entry points:

1. ``score_responses(sessions, responses)`` — scores HUMAN rater forced-choice
   responses for arms (a) archetype-ID and (b) tilt-detection:
     - archetype-ID: accuracy vs chance (1/n) with an exact binomial test +
       a confusion matrix.
     - tilt-detection: signal-detection **d-prime** from hit / false-alarm rates
       (with the standard log-linear correction for 0/1 rates), plus accuracy and
       **Cohen's dz** for the paired within-subject design.

2. ``adaptation_kl(sessions)`` — the AUTOMATABLE arm (c): KL divergence between the
   ON and OFF hero action distributions on matched (street, facing) spots, so the
   adaptation arm yields a perceptibility *number* with no humans. Above a small
   threshold == the adaptation layer measurably changed the hero's behavior on the
   same cards (a necessary condition for it to be perceptible; sufficiency still
   needs the human 2AFC).

Math is dependency-light (uses ``statistics`` + a small erf-inverse) so it runs in
the bare sim container. ``scipy`` is used for the binomial test only if present;
otherwise a normal approximation is used and noted in the output.

This is a TOOL, not a unit test — a ``__main__`` CLI, never collected by pytest.

Usage::

    # automatable adaptation arm:
    python -m experiments.score_2afc --sessions /tmp/2afc_adaptation.json --arm adaptation

    # human-rater arms (responses JSON = {"session_id": choice, ...}):
    python -m experiments.score_2afc --sessions /tmp/2afc_archetype.json \
        --arm archetype --responses /tmp/responses.json
    python -m experiments.score_2afc --sessions /tmp/2afc_tilt.json \
        --arm tilt --responses /tmp/tilt_responses.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Small stats helpers (dependency-light) ──────────────────────────────────────


def _phi_inv(p: float) -> float:
    """Inverse standard-normal CDF (probit) via Acklam's rational approximation.

    Used for d-prime (z-scores of hit/FA rates) without requiring scipy.
    """
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    # Acklam's algorithm.
    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


def _normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def binomial_test_greater(successes: int, n: int, p: float = 0.5) -> Tuple[float, str]:
    """One-sided binomial test P(X >= successes | n, p). Returns (p_value, method).

    Uses scipy if available (exact); otherwise a normal approximation.
    """
    if n == 0:
        return (1.0, "n=0")
    try:
        from scipy.stats import binomtest  # type: ignore

        res = binomtest(successes, n, p, alternative="greater")
        return (float(res.pvalue), "scipy-exact")
    except Exception:
        # Exact via direct summation (fine for small n).
        from math import comb

        pval = sum(comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(successes, n + 1))
        return (float(pval), "exact-sum")


def cohens_dz(diffs: List[float]) -> Optional[float]:
    """Cohen's dz for a within-subject (paired) design: mean(diff)/sd(diff)."""
    if len(diffs) < 2:
        return None
    import statistics

    sd = statistics.stdev(diffs)
    if sd == 0:
        # Degenerate: all pairs scored identically. dz is undefined (would be
        # ±inf) — return None so the JSON output stays valid.
        return None
    return statistics.mean(diffs) / sd


def d_prime(hits: int, n_signal: int, false_alarms: int, n_noise: int) -> Dict[str, float]:
    """d-prime from hit and false-alarm counts with log-linear correction.

    Signal-detection theory: d' = z(hit_rate) - z(false_alarm_rate). The
    log-linear correction (add 0.5 to each cell, 1 to each total) avoids
    infinite z at 0/1 rates — the standard small-n fix.
    """
    hit_rate = (hits + 0.5) / (n_signal + 1)
    fa_rate = (false_alarms + 0.5) / (n_noise + 1)
    dp = _phi_inv(hit_rate) - _phi_inv(fa_rate)
    # Criterion c (response bias).
    c = -0.5 * (_phi_inv(hit_rate) + _phi_inv(fa_rate))
    return {
        "d_prime": dp,
        "criterion_c": c,
        "hit_rate": hit_rate,
        "false_alarm_rate": fa_rate,
    }


# ── Loading ─────────────────────────────────────────────────────────────────────


def load_sessions(path: Path) -> List[dict]:
    payload = json.loads(path.read_text())
    return payload["sessions"]


# ── Arm (c): automatable adaptation-KL ──────────────────────────────────────────


def _kl_divergence(p: Dict[str, float], q: Dict[str, float]) -> float:
    """KL(P || Q) over a shared action support, with Laplace smoothing.

    P = ON distribution, Q = OFF distribution. Smoothing avoids div-by-zero when
    an action appears in one arm but not the other.
    """
    support = set(p) | set(q)
    eps = 1e-9
    p_tot = sum(p.values()) + eps * len(support)
    q_tot = sum(q.values()) + eps * len(support)
    kl = 0.0
    for a in support:
        pa = (p.get(a, 0.0) + eps) / p_tot
        qa = (q.get(a, 0.0) + eps) / q_tot
        kl += pa * math.log(pa / qa)
    return kl


def adaptation_kl(sessions: List[dict], threshold: float = 0.02) -> dict:
    """Arm (c) automatable check: per-pair KL(ON || OFF) of hero action
    distributions on matched (street, facing) spots.

    Returns an overall summary + per-pair + per-bucket detail. The KL is computed
    per (street, facing) bucket then summed (the total behavioral shift across
    spots), and also pooled across all spots for a single headline number.
    """
    # Index sessions by pair_id.
    pairs: Dict[str, Dict[str, dict]] = {}
    for s in sessions:
        if s.get("arm") != "adaptation":
            continue
        pid = s.get("pair_id")
        member = "ON" if s.get("label", {}).get("adaptation_on") else "OFF"
        pairs.setdefault(pid, {})[member] = s

    per_pair = []
    pooled_on: Dict[str, float] = {}
    pooled_off: Dict[str, float] = {}
    for pid, members in sorted(pairs.items()):
        on = members.get("ON")
        off = members.get("OFF")
        if on is None or off is None:
            continue
        on_dist = on.get("hero_action_dist", {})
        off_dist = off.get("hero_action_dist", {})
        buckets = set(on_dist) | set(off_dist)
        bucket_kls = {}
        total_kl = 0.0
        for b in sorted(buckets):
            kl = _kl_divergence(on_dist.get(b, {}), off_dist.get(b, {}))
            bucket_kls[b] = kl
            total_kl += kl
            # Pool flattened action counts for a headline number.
            for a, c in on_dist.get(b, {}).items():
                pooled_on[a] = pooled_on.get(a, 0) + c
            for a, c in off_dist.get(b, {}).items():
                pooled_off[a] = pooled_off.get(a, 0) + c
        per_pair.append(
            {
                "pair_id": pid,
                "summed_bucket_kl": total_kl,
                "bucket_kl": bucket_kls,
                "perceptible": total_kl >= threshold,
            }
        )

    pooled_kl = _kl_divergence(pooled_on, pooled_off)
    mean_summed = sum(p["summed_bucket_kl"] for p in per_pair) / len(per_pair) if per_pair else 0.0
    return {
        "n_pairs": len(per_pair),
        "pooled_kl_on_vs_off": pooled_kl,
        "mean_summed_bucket_kl": mean_summed,
        "threshold": threshold,
        "perceptible": pooled_kl >= threshold,
        "pooled_on_dist": pooled_on,
        "pooled_off_dist": pooled_off,
        "per_pair": per_pair,
        "note": (
            "KL > threshold is a NECESSARY (not sufficient) condition for the "
            "adaptation layer to be player-perceptible: it confirms the layer "
            "measurably changed hero behavior on identical cards. Sufficiency "
            "still requires the human adaptation-2AFC (above-chance choice)."
        ),
    }


# ── Arm (a): archetype-ID accuracy vs chance ─────────────────────────────────────


def score_archetype_id(sessions: List[dict], responses: Dict[str, str]) -> dict:
    """Score human archetype-ID responses against ground truth.

    ``responses`` maps session_id -> the rater's chosen archetype label.
    """
    arch_sessions = [s for s in sessions if s.get("arm") == "archetype"]
    if not arch_sessions:
        return {"error": "no archetype sessions"}
    choices = arch_sessions[0]["label"]["choices"]
    chance = arch_sessions[0]["label"]["chance"]
    n = 0
    correct = 0
    # Confusion matrix: true -> {guessed: count}.
    confusion: Dict[str, Dict[str, int]] = {c: {c2: 0 for c2 in choices} for c in choices}
    for s in arch_sessions:
        sid = s["session_id"]
        if sid not in responses:
            continue
        truth = s["label"]["true_archetype"]
        guess = responses[sid]
        n += 1
        if guess == truth:
            correct += 1
        if truth in confusion and guess in confusion[truth]:
            confusion[truth][guess] += 1
    pval, method = binomial_test_greater(correct, n, chance)
    return {
        "n": n,
        "correct": correct,
        "accuracy": (correct / n) if n else 0.0,
        "chance": chance,
        "p_value_vs_chance": pval,
        "binomial_method": method,
        "above_chance_significant": (pval < 0.05),
        "confusion_matrix": confusion,
    }


# ── Arm (b): tilt-detection d-prime ──────────────────────────────────────────────


def score_tilt(sessions: List[dict], responses: Dict[str, str]) -> dict:
    """Score human tilt-detection responses with signal detection theory.

    ``responses`` maps session_id -> "tilt" or "calm" (the rater's yes/no on "is
    this player on tilt?"). Tilted sessions are SIGNAL; calm are NOISE. A "tilt"
    response on a tilted session is a HIT; on a calm session a FALSE ALARM.
    """
    tilt_sessions = [s for s in sessions if s.get("arm") == "tilt"]
    if not tilt_sessions:
        return {"error": "no tilt sessions"}
    hits = false_alarms = n_signal = n_noise = 0
    # Paired accuracy per pair_id for Cohen's dz (within-subject).
    pair_correct: Dict[str, Dict[str, Optional[bool]]] = {}
    for s in tilt_sessions:
        sid = s["session_id"]
        if sid not in responses:
            continue
        is_tilted = bool(s["label"]["is_tilted"])
        said_tilt = responses[sid].strip().lower() in ("tilt", "tilted", "yes", "1", "true")
        pid = s.get("pair_id")
        pair_correct.setdefault(pid, {"tilted": None, "calm": None})
        if is_tilted:
            n_signal += 1
            if said_tilt:
                hits += 1
            pair_correct[pid]["tilted"] = said_tilt  # correct == said tilt
        else:
            n_noise += 1
            if said_tilt:
                false_alarms += 1
            pair_correct[pid]["calm"] = not said_tilt  # correct == said calm

    dp = d_prime(hits, n_signal, false_alarms, n_noise)
    total = n_signal + n_noise
    accuracy = ((hits + (n_noise - false_alarms)) / total) if total else 0.0
    # Per-pair "score" = #correct of the 2 members (0/1/2) -> dz over pairs.
    diffs = []
    for pid, m in pair_correct.items():
        if m["tilted"] is None or m["calm"] is None:
            continue
        diffs.append(
            (1 if m["tilted"] else 0) + (1 if m["calm"] else 0) - 1.0
        )  # center at chance(1)
    dz = cohens_dz(diffs)
    return {
        "n_signal": n_signal,
        "n_noise": n_noise,
        "hits": hits,
        "false_alarms": false_alarms,
        "accuracy": accuracy,
        **dp,
        "cohens_dz_paired": dz,
        "d_prime_perceptible": dp["d_prime"] >= 1.0,
        "note": "d' >= 1.0 == players reliably feel the tilt (research §2.1b).",
    }


# ── CLI ──────────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Score 2AFC perceptibility sessions.")
    ap.add_argument("--sessions", required=True, help="Sessions JSON from generate_2afc_sessions.")
    ap.add_argument("--arm", choices=["archetype", "tilt", "adaptation"], required=True)
    ap.add_argument(
        "--responses",
        default=None,
        help="Human responses JSON (required for archetype/tilt arms).",
    )
    ap.add_argument(
        "--kl-threshold", type=float, default=0.02, help="Adaptation-KL perceptibility threshold."
    )
    args = ap.parse_args(argv)

    sessions = load_sessions(Path(args.sessions))

    if args.arm == "adaptation":
        result = adaptation_kl(sessions, threshold=args.kl_threshold)
    else:
        if not args.responses:
            print("--responses is required for the archetype/tilt human arms.", file=sys.stderr)
            return 2
        responses = json.loads(Path(args.responses).read_text())
        result = (
            score_archetype_id(sessions, responses)
            if args.arm == "archetype"
            else score_tilt(sessions, responses)
        )

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
