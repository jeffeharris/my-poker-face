import type { ReactNode } from 'react';
import './StadiumLayout.css';

interface StadiumLayoutProps {
  header?: ReactNode;
  leftPanel?: ReactNode;
  children: ReactNode;  // Table content
  rightPanel?: ReactNode;
  bottomCenter?: ReactNode;  // PlayerCommandCenter
}

export function StadiumLayout({
  header,
  leftPanel,
  children,
  rightPanel,
  bottomCenter,
}: StadiumLayoutProps) {
  return (
    <div className="stadium-layout">
      {/* Header spanning full width */}
      {header && (
        <div className="stadium-layout__header">
          {header}
        </div>
      )}

      {/* Left sidebar - Stats Panel */}
      {leftPanel && (
        <div className="stadium-layout__left">
          {leftPanel}
        </div>
      )}

      {/* Main table area */}
      <div className="stadium-layout__table">
        <div className="stadium-layout__table-content">
          {children}
        </div>

        {/* Command center at bottom of table area */}
        {bottomCenter && (
          <div className="stadium-layout__command-center">
            {bottomCenter}
          </div>
        )}
      </div>

      {/* Right sidebar - Activity Feed */}
      {rightPanel && (
        <div className="stadium-layout__right">
          {rightPanel}
        </div>
      )}
    </div>
  );
}
