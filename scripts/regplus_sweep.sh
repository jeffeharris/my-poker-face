#!/usr/bin/env bash
# RegPlus threshold-hardening sweep. One-knob-at-a-time around the validated
# 2026-05-31 config. Reports raw 6max bb/100 on 4 diagnostic cells; raw is
# comparable ACROSS configs here because the opponent field is fixed and the
# hero-seat artifact is ~constant for nearby RegPlus variants.
set -u
cd "$(dirname "$0")/.."
HANDS=800
SEEDS=42,142

# cell label -> opponents arg
declare -A CELLS=(
  [CaseBotV2]="CaseBotV2,CaseBotV2,CaseBotV2,CaseBotV2,CaseBotV2"
  [TAG]="TAG,TAG,TAG,TAG,TAG"
  [jeff]="jeff"
  [TrickyAggro]="TrickyAggro,TrickyAggro,TrickyAggro,TrickyAggro,TrickyAggro"
)
CELL_ORDER=(CaseBotV2 TAG jeff TrickyAggro)

# config label -> env override (empty = baseline/locked defaults)
declare -A CONFIGS=(
  [baseline]=""
  [gate0.7]="-e REGPLUS_FOLD_GATE=0.7"
  [gate0.9]="-e REGPLUS_FOLD_GATE=0.9"
  [obet1.0]="-e REGPLUS_OVERBET_PREMIUM=1.0"
  [obet1.3]="-e REGPLUS_OVERBET_PREMIUM=1.3"
  [strong0.7]="-e REGPLUS_BET_STRONG=0.7"
  [strong1.0]="-e REGPLUS_BET_STRONG=1.0"
  [med0.7]="-e REGPLUS_BET_MEDIUM=0.7"
  [pfwide]="-e REGPLUS_PF_WIDTH=1.0"
)
CONFIG_ORDER=(baseline gate0.7 gate0.9 obet1.0 obet1.3 strong0.7 strong1.0 med0.7 pfwide)

printf '%-12s' "CONFIG"
for c in "${CELL_ORDER[@]}"; do printf '%12s' "$c"; done
printf '\n'
for cfg in "${CONFIG_ORDER[@]}"; do
  printf '%-12s' "$cfg"
  for cell in "${CELL_ORDER[@]}"; do
    mean=$(docker compose exec -T ${CONFIGS[$cfg]} backend \
      python -m experiments.measure_passivity --hero RegPlus \
      --opponents "${CELLS[$cell]}" --hands $HANDS --seeds $SEEDS 2>/dev/null \
      | grep MEAN | grep -oE '[-+][0-9.]+' | head -1)
    printf '%12s' "${mean:-ERR}"
  done
  printf '\n'
done
echo "SWEEP_DONE"
