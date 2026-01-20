/**
 * ThemedSelect - A styled select dropdown that matches our dark theme
 *
 * Usage:
 *   <ThemedSelect value={value} onChange={handleChange}>
 *     <option value="opt1">Option 1</option>
 *     <option value="opt2">Option 2</option>
 *   </ThemedSelect>
 */

import { forwardRef, type SelectHTMLAttributes } from 'react';
import './ThemedSelect.css';

export interface ThemedSelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  /** Size variant */
  size?: 'sm' | 'md' | 'lg';
  /** Full width */
  fullWidth?: boolean;
  /** Error state */
  error?: boolean;
}

export const ThemedSelect = forwardRef<HTMLSelectElement, ThemedSelectProps>(
  ({ size = 'md', fullWidth = false, error = false, className = '', children, ...props }, ref) => {
    const sizeClass = size !== 'md' ? `themed-select--${size}` : '';
    const widthClass = fullWidth ? 'themed-select--full' : '';
    const errorClass = error ? 'themed-select--error' : '';

    return (
      <select
        ref={ref}
        className={`themed-select ${sizeClass} ${widthClass} ${errorClass} ${className}`.trim()}
        {...props}
      >
        {children}
      </select>
    );
  }
);

ThemedSelect.displayName = 'ThemedSelect';

export default ThemedSelect;
