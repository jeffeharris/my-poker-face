import { useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { buildAdminTrail, getAdminParent, type Crumb } from './adminNav';
import { getAdminOrigin } from './adminOrigin';

export interface UseAdminNavResult {
  /** Breadcrumb trail from "Admin" to the current screen. */
  trail: Crumb[];
  /** Go one step up the nav path; at the admin root, exit to the origin. */
  goBack: () => void;
  /** Navigate to a specific breadcrumb path. */
  goCrumb: (path: string) => void;
}

/**
 * The shared engine behind admin navigation. The desktop breadcrumb and the
 * mobile back-arrow both consume this so "back" behaves identically across
 * platforms: up the declared hierarchy, then out to wherever the user came
 * from.
 *
 * `leafLabel` is forwarded to the trail builder for screens whose current
 * leaf is tracked in state rather than the URL (see {@link buildAdminTrail}).
 */
export function useAdminNav(opts?: { leafLabel?: string | null }): UseAdminNavResult {
  const { pathname } = useLocation();
  const navigate = useNavigate();

  const goBack = useCallback(() => {
    const parent = getAdminParent(pathname);
    navigate(parent ?? getAdminOrigin());
  }, [pathname, navigate]);

  const goCrumb = useCallback(
    (path: string) => {
      navigate(path);
    },
    [navigate]
  );

  const trail = buildAdminTrail(pathname, { leafLabel: opts?.leafLabel });

  return { trail, goBack, goCrumb };
}
