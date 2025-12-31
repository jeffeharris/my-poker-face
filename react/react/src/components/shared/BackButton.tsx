import './BackButton.css';

export interface BackButtonProps {
  onClick: () => void;
  /** Visual variant */
  variant?: 'default' | 'light' | 'mobile';
  /** Position style */
  position?: 'absolute' | 'relative' | 'static';
  /** Custom label (defaults to arrow) */
  label?: string;
  /** Additional class name */
  className?: string;
}

/**
 * Unified back button component for consistent navigation across the app.
 *
 * Variants:
 * - default: Semi-transparent with border, for menu headers
 * - light: More visible on dark backgrounds
 * - mobile: Circular button for mobile game header
 */
export function BackButton({
  onClick,
  variant = 'default',
  position = 'absolute',
  label,
  className = '',
}: BackButtonProps) {
  const positionClass = position === 'absolute' ? 'back-button--absolute' :
                        position === 'relative' ? 'back-button--relative' : '';

  return (
    <button
      className={`back-button back-button--${variant} ${positionClass} ${className}`.trim()}
      onClick={onClick}
      aria-label="Go back"
    >
      {variant === 'mobile' ? (
        <span className="back-button__icon">←</span>
      ) : (
        <>
          <span className="back-button__icon">←</span>
          {label && <span className="back-button__label">{label}</span>}
        </>
      )}
    </button>
  );
}
