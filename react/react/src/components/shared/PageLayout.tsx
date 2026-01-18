import type { ReactNode } from 'react';
import './PageLayout.css';

export interface PageLayoutProps {
  /** Page content */
  children: ReactNode;
  /**
   * Layout variant:
   * - 'centered': Content centered vertically (default for simple pages)
   * - 'top': Content starts from top with margin (for scrollable pages)
   */
  variant?: 'centered' | 'top';
  /**
   * Ambient glow color variant for the page background
   */
  glowColor?: 'gold' | 'emerald' | 'sapphire' | 'amethyst' | 'amber' | 'none';
  /**
   * Maximum width of the content container
   * - 'full': No width constraint (ideal for admin/data-dense pages)
   * - '2xl': 1400px (wide but bounded)
   * - 'xl': 1100px (default for most pages)
   */
  maxWidth?: 'sm' | 'md' | 'lg' | 'xl' | '2xl' | 'full';
  /**
   * Additional class name for the outer container
   */
  className?: string;
}

/**
 * PageLayout - Unified page wrapper for menu screens
 *
 * Provides consistent:
 * - Full viewport height
 * - Background gradient
 * - Ambient glow effects
 * - Safe area insets for mobile
 * - Consistent padding and centering
 * - Smooth page transitions
 */
export function PageLayout({
  children,
  variant = 'centered',
  glowColor = 'gold',
  maxWidth = 'lg',
  className = '',
}: PageLayoutProps) {
  const containerClasses = [
    'page-layout',
    `page-layout--${variant}`,
    `page-layout--glow-${glowColor}`,
    className,
  ].filter(Boolean).join(' ');

  const contentClasses = [
    'page-layout__content',
    `page-layout__content--${maxWidth}`,
  ].join(' ');

  return (
    <div className={containerClasses}>
      <div className={contentClasses}>
        {children}
      </div>
    </div>
  );
}
