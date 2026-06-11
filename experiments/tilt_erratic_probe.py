"""EV-DIRECTION probe for the §4 erratic-reads coupling
(TILT_ERRATIC_READS_ENABLED, docs/technical/TILT_EXCURSION_DESIGN.md +
docs/plans/TILT_EV_HARNESS.md). Sibling to `tilt_ev_probe.py` (the signature
probe), but it operates one level down: erratic-reads does NOT shift the action
distribution directly — it only scales the EXPLOITATION layer's strength.

MECHANISM (confirmed against the code):
  `_zone_to_tilt_factor` returns tilt_factor ∈ [0,1]. It enters as
    effective_bias = adaptation_bias · tilt_factor                (controller)
    multiplier     = effective_bias · confidence_ramp · exploitation_strength
                                                          (exploitation.py:1410)
  so tilt_factor is a LINEAR magnitude scalar on the exploitation offsets, plus a
  hard gate: if effective_bias ≤ GATING_FLOOR (0.05) the whole exploitation layer
  returns {} (no offsets). Nothing else reads tilt_factor.

CONSEQUENCE — the flag's EV is linear in the factor:
    ΔEV(on − off) ≈ exploitation_edge · (E[tilt_factor_on] − tilt_factor_off)
  so pricing the FACTOR delta (this probe) is the whole EV question up to two
  scalars — the exploitation edge per read (bb) and the tilted-decision rate — both
  of which need the recorded corpus (the same dependency the signature bb/100 has).

THE TWO ARMS:
  OFF (legacy cliff): composed 1.0 / tilted·overconfident 0.5 / shaken·dissociated
    0.0. A shaken bot's factor is 0.0 → effective_bias 0 → exploitation ALWAYS
    gated off (it forgets every read instantly).
  ON (erratic taper): factor = 1 − intensity·U(0,1), one draw per decision.
    E[factor] = 1 − 0.5·intensity. This sits ABOVE the cliff for tilted (0.75 > 0.5
    at intensity 0.5) and FAR above for shaken (0.5–0.75 vs 0.0). So the "pure
    attenuator, can only reduce a read's edge" safety note is true only relative to
    a FULL read (factor = 1) — relative to the actual OFF baseline, turning the flag
    ON makes a tilted/shaken bot exploit MORE on average, concentrated in the shaken
    states the cliff used to zero out. Direction: exploitation is +EV, so
    erratic-reads is mildly +EV on average vs off, plus added variance.

This probe quantifies, per adaptation_bias tier and per tilt state, the exploitation
STRENGTH the bot actually applies (gate-aware): m = adaptation_bias·factor if that
exceeds GATING_FLOOR else 0. It reports m_off (cliff), E[m_on] (erratic, integrated
over U), Δm, and the fire-rate on each arm. Δm > 0 ⇒ ON exploits more ⇒ +EV if reads
are +EV. confidence_ramp and exploitation_strength are common to both arms (assumed
1.0 here) so they cancel in Δm.

Run: docker compose exec -T backend python3 -m experiments.tilt_erratic_probe
"""

from __future__ import annotations

import json

from poker.strategy.exploitation import GATING_FLOOR

U_STEPS = 2000  # deterministic integration over U ~ Unif(0,1); no RNG needed

# OFF cliff: state -> fixed factor (the legacy _zone_to_tilt_factor when flag off).
CLIFF = {
    'tilted': 0.5,
    'overconfident': 0.5,
    'shaken': 0.0,
    'dissociated': 0.0,
}

# Representative intensities by severity band (emotional_state.intensity).
INTENSITIES = [('mild 0.35', 0.35), ('moderate 0.55', 0.55), ('severe 0.80', 0.80)]


def _strength(bias: float, factor: float) -> float:
    """Gate-aware exploitation strength: 0 if effective_bias ≤ GATING_FLOOR."""
    eff = bias * factor
    return eff if eff > GATING_FLOOR else 0.0


def _erratic_expectation(bias: float, intensity: float) -> tuple[float, float]:
    """E[strength] and fire-rate under the erratic taper, integrated over U.

    factor(U) = clamp(1 − intensity·U, 0, 1); strength gated at GATING_FLOOR.
    """
    total = 0.0
    fires = 0
    for k in range(U_STEPS):
        u = (k + 0.5) / U_STEPS  # midpoint rule
        factor = max(0.0, min(1.0, 1.0 - intensity * u))
        s = _strength(bias, factor)
        total += s
        if s > 0.0:
            fires += 1
    return total / U_STEPS, fires / U_STEPS


def main() -> None:
    with open('poker/personalities.json') as f:
        personas = json.load(f).get('personalities', {})
    biases = [
        float(c['anchors'].get('adaptation_bias', 0) or 0)
        for c in personas.values()
        if isinstance(c, dict)
        and 'anchors' in c
        and float(c['anchors'].get('recovery_rate', 0) or 0) > 0
    ]

    tiers = [
        ('low <0.30', lambda b: b < 0.30),
        ('mid 0.30-0.55', lambda b: 0.30 <= b < 0.55),
        ('high >=0.55', lambda b: b >= 0.55),
    ]

    print('=' * 96)
    print(f'ERRATIC-READS — factor-level EV-direction probe ({len(biases)} personas)')
    print('  m = exploitation STRENGTH applied (adaptation_bias·tilt_factor, gated at')
    print(
        f'  GATING_FLOOR={GATING_FLOOR}). Δm = E[m_on(erratic)] − m_off(cliff). Δm>0 ⇒ ON exploits'
    )
    print('  MORE ⇒ +EV if reads are +EV. "fire" = P(exploitation layer emits offsets).')
    print('=' * 96)

    for state in ('tilted', 'shaken'):
        cliff_factor = CLIFF[state]
        for int_label, intensity in INTENSITIES:
            print(f'\n  [{state}]  intensity {int_label}   (cliff factor = {cliff_factor})')
            print(
                f'    {"adaptation tier":18s} {"n":>3s} {"m_off":>7s} {"fire_off":>9s} '
                f'{"E[m_on]":>8s} {"fire_on":>8s} {"Δm":>8s}'
            )
            for label, pred in tiers:
                members = [b for b in biases if pred(b)]
                if not members:
                    continue
                n = len(members)
                m_off = sum(_strength(b, cliff_factor) for b in members) / n
                fire_off = sum(1 for b in members if _strength(b, cliff_factor) > 0) / n
                on = [_erratic_expectation(b, intensity) for b in members]
                m_on = sum(x[0] for x in on) / n
                fire_on = sum(x[1] for x in on) / n
                print(
                    f'    {label:18s} {n:3d} {m_off:7.3f} {fire_off:9.2f} '
                    f'{m_on:8.3f} {fire_on:8.2f} {m_on - m_off:+8.3f}'
                )

    print('\n  READING IT: Δm is the fraction by which the flag rescales the exploitation edge.')
    print(
        '  For TILTED states Δm is small and ~0 at high intensity (erratic mean ≈ the 0.5 cliff).'
    )
    print(
        '  For SHAKEN states Δm is strongly POSITIVE at every tier — the cliff zeroed exploitation'
    )
    print('  (fire_off≈0), the erratic taper restores it (fire_on high). So erratic-reads does not')
    print(
        '  COST EV vs off; it RECOVERS forgone read-edge in the shaken band, + adds variance. The'
    )
    print('  "unreliable reads" believability win coincides with a mean exploitation INCREASE. To')
    print(
        '  turn Δm into bb/100: × exploitation_edge_bb/read × tilted-decision rate (both from the'
    )
    print('  recorded corpus — TILT_EV_HARNESS part (a), shared with the signature). Sign caveat:')
    print(
        '  +EV holds only while the read is +EV; against an opponent actively inducing, a stronger'
    )
    print('  read can be -EV — which is the believability point, just not a catastrophe (clamped).')


if __name__ == '__main__':
    main()
