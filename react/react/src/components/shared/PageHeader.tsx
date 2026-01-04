import type { ReactNode } from 'react';
import { BackButton } from './BackButton';
import './PageHeader.css';

export interface PageHeaderProps {
  /** Main title */
  title: string;
  /** Optional subtitle below title */
  subtitle?: string;
  /** Back button click handler (if provided, shows back button) */
  onBack?: () => void;
  /** Optional content for right side of header */
  rightContent?: ReactNode;
  /** Title gradient variant */
  titleVariant?: 'primary' | 'themed' | 'none';
  /** Additional class name */
  className?: string;
}

/**
 * Unified page header for menu screens.
 *
 * Provides consistent layout with:
 * - Optional back button (top-left)
 * - Centered title with optional gradient
 * - Optional subtitle
 * - Optional right content slot
 */
export function PageHeader({
  title,
  subtitle,
  onBack,
  rightContent,
  titleVariant = 'primary',
  className = '',
}: PageHeaderProps) {
  const titleClass = titleVariant === 'primary' ? 'page-header__title--gradient-primary' :
                     titleVariant === 'themed' ? 'page-header__title--gradient-themed' : '';

  return (
    <header className={`page-header ${className}`.trim()}>
      {onBack && (
        <BackButton onClick={onBack} position="absolute" />
      )}

      <div className="page-header__content">
        <h1 className={`page-header__title ${titleClass}`.trim()}>
          {title}
        </h1>
        {subtitle && (
          <p className="page-header__subtitle">{subtitle}</p>
        )}
      </div>

      {rightContent && (
        <div className="page-header__right">
          {rightContent}
        </div>
      )}
    </header>
  );
}
