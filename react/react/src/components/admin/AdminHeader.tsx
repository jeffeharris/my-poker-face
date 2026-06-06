import { ArrowLeft } from 'lucide-react';
import { AdminBreadcrumbs } from './AdminBreadcrumbs';
import { useAdminNav } from './useAdminNav';

interface AdminHeaderProps {
  /** Current screen title (shown below the breadcrumb). */
  title: string;
  /** Optional one-line description under the title. */
  subtitle?: string;
  /** Live leaf label for the breadcrumb (e.g. "Capture #42"); see
   *  {@link AdminBreadcrumbs}. Pass `null` to drop the leaf crumb. */
  leafLabel?: string | null;
}

/**
 * Shared desktop admin header: a deterministic back-arrow (one step up the
 * nav path, or out to the entry origin at the root) plus the breadcrumb trail
 * and the screen title. Replaces the per-wrapper `admin-main__header` blocks
 * so every admin screen navigates identically.
 */
export function AdminHeader({ title, subtitle, leafLabel }: AdminHeaderProps) {
  const { trail, goBack, goCrumb } = useAdminNav({ leafLabel });

  return (
    <header className="admin-main__header">
      <button
        className="admin-main__back admin-back-button admin-back-button--icon"
        onClick={goBack}
        aria-label="Go back"
      >
        <ArrowLeft size={20} />
      </button>
      <div className="admin-main__header-text">
        <AdminBreadcrumbs trail={trail} onCrumb={goCrumb} />
        <h1 className="admin-main__title">{title}</h1>
        {subtitle && <p className="admin-main__subtitle">{subtitle}</p>}
      </div>
    </header>
  );
}
