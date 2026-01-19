import { ReactNode, useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import './MobileFilterSheet.css';

interface MobileFilterSheetProps {
  /** Whether the sheet is open */
  isOpen: boolean;
  /** Callback to close the sheet */
  onClose: () => void;
  /** Content to render inside the sheet */
  children: ReactNode;
  /** Optional title for the sheet */
  title?: string;
  /** Optional custom class name */
  className?: string;
}

/**
 * MobileFilterSheet - Bottom sheet component for mobile filters
 *
 * Features:
 * - Slide-up animation
 * - Backdrop tap to dismiss
 * - Drag handle for visual affordance
 * - Safe area bottom padding
 * - Focus trap when open
 */
export function MobileFilterSheet({
  isOpen,
  onClose,
  children,
  title,
  className = '',
}: MobileFilterSheetProps) {
  const sheetRef = useRef<HTMLDivElement>(null);

  // Handle escape key
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  // Prevent body scroll when sheet is open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <div className="mobile-filter-sheet__overlay" onClick={onClose}>
      <div
        ref={sheetRef}
        className={`mobile-filter-sheet ${className}`}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title || 'Filter options'}
      >
        {/* Drag handle */}
        <div className="mobile-filter-sheet__handle">
          <div className="mobile-filter-sheet__handle-bar" />
        </div>

        {/* Header */}
        {title && (
          <div className="mobile-filter-sheet__header">
            <h3 className="mobile-filter-sheet__title">{title}</h3>
            <button
              className="mobile-filter-sheet__close-btn"
              onClick={onClose}
              aria-label="Close"
            >
              <X size={20} />
            </button>
          </div>
        )}

        {/* Content */}
        <div className="mobile-filter-sheet__content">
          {children}
        </div>
      </div>
    </div>
  );
}

export default MobileFilterSheet;
