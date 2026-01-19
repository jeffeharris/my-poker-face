import { Routes, Route, Navigate, useParams, useNavigate } from 'react-router-dom';
import { AdminDashboard } from './AdminDashboard';
import type { AdminTab } from './AdminSidebar';

const VALID_TABS: AdminTab[] = ['personalities', 'analyzer', 'playground', 'experiments', 'templates', 'settings', 'debug'];

function AdminTabWrapper() {
  const { tab } = useParams<{ tab: string }>();
  const navigate = useNavigate();

  // Validate the tab parameter
  const validTab = VALID_TABS.includes(tab as AdminTab) ? (tab as AdminTab) : 'personalities';

  const handleBack = () => {
    navigate('/menu');
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

export function AdminRoutes() {
  return (
    <Routes>
      <Route index element={<Navigate to="/admin/personalities" replace />} />
      <Route path=":tab" element={<AdminTabWrapper />} />
    </Routes>
  );
}
