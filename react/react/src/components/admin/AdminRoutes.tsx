import { useState } from 'react';
import { Routes, Route, Navigate, useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { AdminDashboard, SIDEBAR_ITEMS } from './AdminDashboard';
import { AdminSidebar } from './AdminSidebar';
import { ExperimentDetail } from './ExperimentDesigner/ExperimentDetail';
import { useViewport } from '../../hooks/useViewport';
import type { AdminTab } from './AdminSidebar';

const VALID_TABS: AdminTab[] = ['personalities', 'analyzer', 'playground', 'experiments', 'templates', 'settings', 'debug'];

/**
 * Wrapper for experiment detail view with URL params
 */
function ExperimentDetailWrapper() {
  const { experimentId } = useParams<{ experimentId: string }>();
  const navigate = useNavigate();
  const { isMobile } = useViewport();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const handleBack = () => {
    navigate('/admin/experiments');
  };

  const handleEditInLabAssistant = (experiment: Parameters<NonNullable<React.ComponentProps<typeof ExperimentDetail>['onEditInLabAssistant']>>[0]) => {
    navigate('/admin/experiments', { state: { editExperiment: experiment } });
  };

  const handleBuildFromSuggestion = (
    experiment: Parameters<NonNullable<React.ComponentProps<typeof ExperimentDetail>['onBuildFromSuggestion']>>[0],
    suggestion: Parameters<NonNullable<React.ComponentProps<typeof ExperimentDetail>['onBuildFromSuggestion']>>[1]
  ) => {
    navigate('/admin/experiments', { state: { buildFromSuggestion: { experiment, suggestion } } });
  };

  if (!experimentId || isNaN(parseInt(experimentId, 10))) {
    return <Navigate to="/admin/experiments" replace />;
  }

  const experimentIdNum = parseInt(experimentId, 10);

  // Mobile layout
  if (isMobile) {
    return (
      <div className="admin-dashboard-layout admin-dashboard-layout--mobile">
        <div className="admin-main__content admin-main__content--mobile">
          <ExperimentDetail
            experimentId={experimentIdNum}
            onBack={handleBack}
            onEditInLabAssistant={handleEditInLabAssistant}
            onBuildFromSuggestion={handleBuildFromSuggestion}
          />
        </div>
      </div>
    );
  }

  // Desktop layout with sidebar
  return (
    <div className="admin-dashboard-layout">
      <AdminSidebar
        items={SIDEBAR_ITEMS}
        activeTab="experiments"
        onTabChange={(tab) => navigate(`/admin/${tab}`)}
        collapsed={sidebarCollapsed}
        onCollapsedChange={setSidebarCollapsed}
      />
      <main className="admin-main">
        <header className="admin-main__header">
          <button
            className="admin-main__back"
            onClick={handleBack}
            aria-label="Go back to experiments"
          >
            <ArrowLeft size={20} />
          </button>
          <div className="admin-main__header-text">
            <h1 className="admin-main__title">Experiment Details</h1>
            <p className="admin-main__subtitle">View experiment results and analysis</p>
          </div>
        </header>
        <div className="admin-main__content">
          <ExperimentDetail
            experimentId={experimentIdNum}
            onBack={handleBack}
            onEditInLabAssistant={handleEditInLabAssistant}
            onBuildFromSuggestion={handleBuildFromSuggestion}
          />
        </div>
      </main>
    </div>
  );
}

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
      {/* Experiment detail route - must come before :tab to match first */}
      <Route path="experiments/:experimentId" element={<ExperimentDetailWrapper />} />
      <Route path=":tab" element={<AdminTabWrapper />} />
    </Routes>
  );
}
