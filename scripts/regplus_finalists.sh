#!/usr/bin/env bash
# RegPlus finalist validation: combine the two sweep wins (fold-gate 0.7 +
# overbet 1.3), probe the gate floor, and add the competent punisher cell at
# higher hand counts. Raw 6max bb/100, comparable across configs (fixed fields).
set -u
cd "$(dirname "$0")/.."
HANDS=1200
SEEDS=42,142,242

declare -A CELLS=(
  [CaseBotV2]="CaseBotV2,CaseBotV2,CaseBotV2,CaseBotV2,CaseBotV2"
  [TAG]="TAG,TAG,TAG,TAG,TAG"
  [punisher]="punisher"
  [jeff]="jeff"
  [Maniac]="ManiacBot,ManiacBot,ManiacBot,ManiacBot,ManiacBot"
)
CELL_ORDER=(CaseBotV2 TAG punisher jeff Maniac)

declare -A CONFIGS=(
  [baseline]=""
  [g0.7_o1.3]="-e REGPLUS_FOLD_GATE=0.7 -e REGPLUS_OVERBET_PREMIUM=1.3"
  [g0.65_o1.3]="-e REGPLUS_FOLD_GATE=0.65 -e REGPLUS_OVERBET_PREMIUM=1.3"
  [g0.6_o1.3]="-e REGPLUS_FOLD_GATE=0.6 -e REGPLUS_OVERBET_PREMIUM=1.3"
)
CONFIG_ORDER=(baseline g0.7_o1.3 g0.65_o1.3 g0.6_o1.3)

printf '%-12s' "CONFIG"
for c in "${CELL_ORDER[@]}"; do printf '%11s' "$c"; done
printf '\n'
for cfg in "${CONFIG_ORDER[@]}"; do
  printf '%-12s' "$cfg"
  for cell in "${CELL_ORDER[@]}"; do
    mean=$(docker compose exec -T ${CONFIGS[$cfg]} backend \
      python -m experiments.measure_passivity --hero RegPlus \
      --opponents "${CELLS[$cell]}" --hands $HANDS --seeds $SEEDS 2>/dev/null \
      | grep MEAN | grep -oE '[-+][0-9.]+' | head -1)
    printf '%11s' "${mean:-ERR}"
  done
  printf '\n'
done
echo "FINALISTS_DONE"
