import { Fragment } from 'react';
import { ChevronRight } from 'lucide-react';
import { useViewport } from '../../hooks/useViewport';
import type { Crumb } from './adminNav';
import './AdminBreadcrumbs.css';

interface AdminBreadcrumbsProps {
  /** Breadcrumb trail, root → current. Computed once by the parent so the
   *  nav hook isn't run twice per render. */
  trail: Crumb[];
  /** Navigate to a crumb's path. */
  onCrumb: (path: string) => void;
}

/**
 * Desktop-only clickable breadcrumb trail for the admin shell. Renders nothing
 * on mobile, where the MenuBar back-arrow handles "up the nav path" instead.
 * Purely presentational — {@link AdminHeader} owns the nav state.
 */
export function AdminBreadcrumbs({ trail, onCrumb }: AdminBreadcrumbsProps) {
  const { isMobile } = useViewport();

  if (isMobile || trail.length === 0) return null;

  return (
    <nav className="admin-breadcrumbs" aria-label="Breadcrumb">
      {trail.map((crumb, i) => {
        const isLast = i === trail.length - 1;
        return (
          <Fragment key={`${crumb.label}-${i}`}>
            {crumb.path && !isLast ? (
              <button
                type="button"
                className="admin-breadcrumbs__link"
                onClick={() => onCrumb(crumb.path as string)}
              >
                {crumb.label}
              </button>
            ) : (
              <span
                className="admin-breadcrumbs__current"
                aria-current={isLast ? 'page' : undefined}
              >
                {crumb.label}
              </span>
            )}
            {!isLast && (
              <ChevronRight size={14} className="admin-breadcrumbs__sep" aria-hidden="true" />
            )}
          </Fragment>
        );
      })}
    </nav>
  );
}
