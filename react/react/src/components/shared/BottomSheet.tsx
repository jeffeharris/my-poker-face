import { type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import './BottomSheet.css';

export interface BottomSheetProps {
  isOpen: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  /** On desktop: 'sheet' stays as bottom sheet, 'modal' becomes a centered dialog. Default: 'sheet'. */
  desktopMode?: 'sheet' | 'modal';
}

export function BottomSheet({
  isOpen,
  onClose,
  title,
  children,
  desktopMode = 'sheet',
}: BottomSheetProps) {
  if (!isOpen) return null;

  // Portaled to <body> so the fixed sheet + backdrop escape any ancestor
  // stacking context (e.g. PageLayout). See CharacterDetailCard.
  return createPortal(
    <>
      <div className="bottom-sheet-backdrop" onClick={onClose} />
      <div className={`bottom-sheet${desktopMode === 'modal' ? ' bottom-sheet--modal' : ''}`}>
        <div className="bottom-sheet__handle">
          <div className="bottom-sheet__handle-bar" />
        </div>
        <div className="bottom-sheet__header">
          <h3 className="bottom-sheet__title">{title}</h3>
          <button className="bottom-sheet__close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <div className="bottom-sheet__content">{children}</div>
      </div>
    </>,
    document.body
  );
}
