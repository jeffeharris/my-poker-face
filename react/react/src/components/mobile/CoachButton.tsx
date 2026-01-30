import { useState, useRef, useCallback } from 'react';
import { GraduationCap } from 'lucide-react';
import './CoachButton.css';

interface CoachButtonProps {
  onClick: () => void;
  hasNewInsight: boolean;
}

const STORAGE_KEY = 'coach_button_pos';
const DRAG_THRESHOLD = 10;

function loadPosition(): { x: number; y: number } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return { x: 16, y: 120 };
}

export function CoachButton({ onClick, hasNewInsight }: CoachButtonProps) {
  const [pos, setPos] = useState(loadPosition);
  const touchStart = useRef({ x: 0, y: 0, btnX: 0, btnY: 0 });
  const totalMove = useRef(0);
  const isDragging = useRef(false);
  const btnRef = useRef<HTMLButtonElement>(null);

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    const touch = e.touches[0];
    touchStart.current = {
      x: touch.clientX,
      y: touch.clientY,
      btnX: pos.x,
      btnY: pos.y,
    };
    totalMove.current = 0;
    isDragging.current = false;
  }, [pos]);

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    const touch = e.touches[0];
    const dx = touch.clientX - touchStart.current.x;
    const dy = touch.clientY - touchStart.current.y;
    totalMove.current = Math.abs(dx) + Math.abs(dy);

    if (totalMove.current > DRAG_THRESHOLD) {
      isDragging.current = true;
      // Position is offset from bottom-right corner
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const newX = Math.max(0, Math.min(vw - 56, vw - (touchStart.current.btnX + 28) - dx)) - (vw - 56 - touchStart.current.btnX) + touchStart.current.btnX;
      const newRight = touchStart.current.btnX - dx;
      const newBottom = touchStart.current.btnY - dy;

      setPos({
        x: Math.max(8, Math.min(vw - 64, newRight)),
        y: Math.max(8, Math.min(vh - 120, newBottom)),
      });
    }
  }, []);

  const handleTouchEnd = useCallback(() => {
    if (!isDragging.current) {
      onClick();
      return;
    }

    // Snap to nearest horizontal edge
    const vw = window.innerWidth;
    setPos(prev => {
      const centerX = vw - prev.x - 28;
      const snapped = {
        x: centerX < vw / 2 ? vw - 64 : 16,
        y: prev.y,
      };
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(snapped));
      } catch { /* ignore */ }
      return snapped;
    });
  }, [onClick]);

  return (
    <button
      ref={btnRef}
      className={`coach-fab ${hasNewInsight ? 'has-insight' : ''}`}
      style={{
        right: `${pos.x}px`,
        bottom: `${pos.y}px`,
      }}
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
      onClick={(e) => {
        // Desktop click fallback (touch handlers handle mobile)
        if (!('ontouchstart' in window)) {
          onClick();
        }
        e.preventDefault();
      }}
      aria-label="Open poker coach"
    >
      <GraduationCap size={26} />
      {hasNewInsight && <span className="coach-fab-badge" />}
    </button>
  );
}
