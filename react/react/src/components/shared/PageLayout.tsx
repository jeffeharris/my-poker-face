import type { ReactNode } from 'react';
import './PageLayout.css';

export interface PageLayoutProps {
  /** Page content */
  children: ReactNode;
  /**
   * Layout variant:
   * - 'centered': Content centered vertically (default for simple pages)
   * - 'top': Content starts from top with margin (for scrollable pages)
   * - 'fixed': Content fills viewport without scrolling (for menus with MenuBar)
   */
  variant?: 'centered' | 'top' | 'fixed';
  /**
   * Ambient glow color variant for the page background
   */
  glowColor?: 'gold' | 'emerald' | 'sapphire' | 'amethyst' | 'amber' | 'none';
  /**
   * Maximum width of the content container
   */
  maxWidth?: 'sm' | 'md' | 'lg' | 'xl';
  /**
   * Whether page has a MenuBar (adds top padding to account for fixed header)
   */
  hasMenuBar?: boolean;
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
  hasMenuBar = false,
  className = '',
}: PageLayoutProps) {
  const containerClasses = [
    'page-layout',
    `page-layout--${variant}`,
    `page-layout--glow-${glowColor}`,
    hasMenuBar && 'page-layout--has-menu-bar',
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
