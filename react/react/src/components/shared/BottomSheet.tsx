import { type ReactNode } from 'react';
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

export function BottomSheet({ isOpen, onClose, title, children, desktopMode = 'sheet' }: BottomSheetProps) {
  if (!isOpen) return null;

  return (
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
        <div className="bottom-sheet__content">
          {children}
        </div>
      </div>
    </>
  );
}
