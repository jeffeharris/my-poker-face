import { Routes, Route, Navigate, useParams, useNavigate } from 'react-router-dom';
import { AdminDashboard } from './AdminDashboard';
import { useViewport } from '../../hooks/useViewport';
import type { AdminTab } from './AdminSidebar';

const VALID_TABS: AdminTab[] = ['personalities', 'analyzer', 'playground', 'experiments', 'templates', 'settings', 'debug'];

function AdminTabWrapper() {
  const { tab } = useParams<{ tab: string }>();
  const navigate = useNavigate();
  const { isMobile } = useViewport();

  // Validate the tab parameter
  const validTab = VALID_TABS.includes(tab as AdminTab) ? (tab as AdminTab) : 'personalities';

  const handleBack = () => {
    // On mobile, go back to admin menu; on desktop, go to main menu
    if (isMobile) {
      navigate('/admin');
    } else {
      navigate('/menu');
    }
  };

  const handleTabChange = (newTab: AdminTab) => {
    navigate(`/admin/${newTab}`);
  };

  return (
    <AdminDashboard
      onBack={handleBack}
      initialTab={validTab}
      onTabChange={handleTabChange}
    />
  );
}

function AdminIndex() {
  const navigate = useNavigate();
  const { isMobile } = useViewport();

  // On desktop, redirect to personalities tab
  // On mobile, show the menu via AdminDashboard with no tab selected
  if (!isMobile) {
    return <Navigate to="/admin/personalities" replace />;
  }

  const handleBack = () => {
    navigate('/menu');
  };

  const handleTabChange = (newTab: AdminTab) => {
    navigate(`/admin/${newTab}`);
  };

  return (
    <AdminDashboard
      onBack={handleBack}
      initialTab={undefined}
      onTabChange={handleTabChange}
    />
  );
}

export function AdminRoutes() {
  return (
    <Routes>
      <Route index element={<AdminIndex />} />
      <Route path=":tab" element={<AdminTabWrapper />} />
    </Routes>
  );
}
