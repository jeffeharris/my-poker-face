#!/bin/bash
echo "Fixing TypeScript errors for React build..."

# Fix PokerTable.tsx - remove unused startPolling
sed -i "s/const { startPolling, stopPolling } = usePolling();/const { stopPolling } = usePolling();/" react/react/src/components/game/PokerTable/PokerTable.tsx

# Fix null assignment issue
sed -i "s/winner={winnerData?.winner || null}/winner={winnerData?.winner || undefined}/" react/react/src/components/game/PokerTable/PokerTable.tsx

# Fix ReactNode import
sed -i "s/import { ReactNode } from 'react';/import type { ReactNode } from 'react';/" react/react/src/components/game/PokerTableLayout/PokerTableLayout.tsx

# Fix unused variables in ThemedGameSelector
sed -i "/const \[loading, setLoading\] = useState(false);/d" react/react/src/components/menus/ThemedGameSelector.tsx

# Fix unused socket in PressureStats
sed -i "s/const { gameState, socket } = useContext(GameContext);/const { gameState } = useContext(GameContext);/" react/react/src/components/stats/PressureStats.tsx

# Fix NodeJS namespace
sed -i "s/intervalRef: React.MutableRefObject<NodeJS.Timeout | null>;/intervalRef: React.MutableRefObject<number | null>;/" react/react/src/hooks/usePolling.ts

# Fix tsconfig issues - remove erasableSyntaxOnly
sed -i '/"erasableSyntaxOnly"/d' react/react/tsconfig.app.json
sed -i '/"erasableSyntaxOnly"/d' react/react/tsconfig.node.json

echo "TypeScript errors fixed!"