import type { ReactNode } from 'react';
import './PokerTableLayout.css';

interface PokerTableLayoutProps {
  children: ReactNode;
  chatPanel: ReactNode;
  debugPanel?: ReactNode;
  actionButtons?: ReactNode;
  showDebug?: boolean;
}

export function PokerTableLayout({ 
  children, 
  chatPanel, 
  debugPanel, 
  actionButtons,
  showDebug = false 
}: PokerTableLayoutProps) {
  return (
    <div className="poker-layout">
      {/* Main game area */}
      <div className="poker-layout__main">
        <div className="poker-layout__table-container">
          {children}
        </div>
        
        {/* Action buttons at bottom of main area */}
        {actionButtons && (
          <div className="poker-layout__actions">
            {actionButtons}
          </div>
        )}
      </div>

      {/* Right sidebar for chat */}
      <div className="poker-layout__sidebar">
        {chatPanel}
      </div>

      {/* Bottom panel for debug (collapsible) */}
      {showDebug && debugPanel && (
        <div className="poker-layout__debug">
          {debugPanel}
        </div>
      )}
    </div>
  );
}