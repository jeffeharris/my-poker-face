import { useEffect, useRef, useState, type ReactNode } from 'react';
import './CollapsibleSection.css';

export interface CollapsibleSectionProps {
  title: string;
  icon: ReactNode;
  isOpen: boolean;
  onToggle: () => void;
  children: ReactNode;
  badge?: string;
}

/**
 * Shared admin accordion section. Externally controlled (the caller owns the
 * open state) with a smooth height animation measured from the content's
 * scrollHeight. Extracted from PersonalityManager so other admin editors
 * (UnifiedSettings, ConfigPreview, …) can drop their private copies onto it.
 */
export function CollapsibleSection({
  title,
  icon,
  isOpen,
  onToggle,
  children,
  badge,
}: CollapsibleSectionProps) {
  const contentRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number | undefined>(undefined);

  useEffect(() => {
    if (contentRef.current) {
      setHeight(isOpen ? contentRef.current.scrollHeight : 0);
    }
  }, [isOpen, children]);

  return (
    <div className={`admin-collapsible ${isOpen ? 'admin-collapsible--open' : ''}`}>
      <button className="admin-collapsible__header" onClick={onToggle} type="button">
        <span className="admin-collapsible__icon">{icon}</span>
        <span className="admin-collapsible__title">{title}</span>
        {badge && <span className="admin-collapsible__badge">{badge}</span>}
        <span className="admin-collapsible__chevron">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path
              d="M5 7.5L10 12.5L15 7.5"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
      </button>
      <div
        className="admin-collapsible__content"
        style={{ height: height !== undefined ? `${height}px` : 'auto' }}
      >
        <div ref={contentRef} className="admin-collapsible__inner">
          {children}
        </div>
      </div>
    </div>
  );
}
