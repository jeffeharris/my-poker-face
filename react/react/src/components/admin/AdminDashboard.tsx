import { useState, useEffect, useCallback, lazy, Suspense } from 'react';
import { ChevronRight, MessageSquare } from 'lucide-react';
import { AdminSidebar, type AdminTab } from './AdminSidebar';
import { AdminHeader } from './AdminHeader';
import type { AssistantPanelProps } from './ExperimentDesigner';
import { AdminMenuContainer } from './AdminMenuContainer';
import { AdminOverview } from './AdminOverview';
import { PageLayout, PageHeader, MenuBar } from '../shared';

// Heavy admin tools are lazy-loaded so they don't bloat the main AdminRoutes
// chunk. Each is a NAMED export, so map it onto `default` for React.lazy.
const PersonalityManager = lazy(() =>
  import('./PersonalityManager').then((m) => ({ default: m.PersonalityManager }))
);
const DecisionAnalyzer = lazy(() =>
  import('./DecisionAnalyzer').then((m) => ({ default: m.DecisionAnalyzer }))
);
const PromptPlayground = lazy(() =>
  import('../debug/PromptPlayground').then((m) => ({ default: m.PromptPlayground }))
);
const ExperimentDesigner = lazy(() =>
  import('./ExperimentDesigner/ExperimentDesigner').then((m) => ({ default: m.ExperimentDesigner }))
);
const ExperimentChat = lazy(() =>
  import('./ExperimentDesigner/ExperimentChat').then((m) => ({ default: m.ExperimentChat }))
);
const PromptPresetManager = lazy(() =>
  import('./PromptPresetManager').then((m) => ({ default: m.PromptPresetManager }))
);
const TemplateEditor = lazy(() =>
  import('./TemplateEditor').then((m) => ({ default: m.TemplateEditor }))
);
const DebugTools = lazy(() => import('./DebugTools').then((m) => ({ default: m.DebugTools })));
const ChipLedgerPanel = lazy(() =>
  import('./ChipLedgerPanel').then((m) => ({ default: m.ChipLedgerPanel }))
);
const CashWhereaboutsPanel = lazy(() =>
  import('./CashWhereaboutsPanel').then((m) => ({ default: m.CashWhereaboutsPanel }))
);
const RangeExplorer = lazy(() =>
  import('./RangeExplorer').then((m) => ({ default: m.RangeExplorer }))
);
const CoachEffectivenessPanel = lazy(() =>
  import('./CoachEffectivenessPanel').then((m) => ({ default: m.CoachEffectivenessPanel }))
);
const HandReplayBrowser = lazy(() =>
  import('./HandReplay').then((m) => ({ default: m.HandReplayBrowser }))
);
const UnifiedSettings = lazy(() =>
  import('./UnifiedSettings').then((m) => ({ default: m.UnifiedSettings }))
);
const UserManagement = lazy(() =>
  import('./UserManagement').then((m) => ({ default: m.UserManagement }))
);

const toolFallback = (
  <div style={{ padding: '2rem', textAlign: 'center', color: 'rgba(255,255,255,0.5)' }}>
    Loading…
  </div>
);
import { useViewport } from '../../hooks/useViewport';
import { SIDEBAR_ITEMS } from './adminSidebarItems';
import './AdminDashboard.css';
import '../menus/TournamentMenu.css';

interface AdminDashboardProps {
  onBack: () => void;
  initialTab?: AdminTab;
  onTabChange?: (tab: AdminTab) => void;
  /** Called when a capture is selected in the analyzer (for URL navigation) */
  onCaptureSelect?: (captureId: number | null) => void;
}

export function AdminDashboard({
  onBack,
  initialTab,
  onTabChange,
  onCaptureSelect,
}: AdminDashboardProps) {
  const { isMobile } = useViewport();
  const [activeTab, setActiveTab] = useState<AdminTab | undefined>(initialTab);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [assistantPanelProps, setAssistantPanelProps] = useState<AssistantPanelProps | null>(null);
  const [isDesignMode, setIsDesignMode] = useState(false);

  // Decision Analyzer detail mode state
  const [analyzerInDetailMode, setAnalyzerInDetailMode] = useState(false);
  const [analyzerBackToList, setAnalyzerBackToList] = useState<(() => void) | null>(null);

  // Sync activeTab with initialTab when it changes (URL navigation)
  useEffect(() => {
    setActiveTab(initialTab);
  }, [initialTab]);

  // Wrapper to update both state and notify parent
  const handleTabChange = useCallback(
    (tab: AdminTab) => {
      setActiveTab(tab);
      onTabChange?.(tab);
      // Clear assistant panel when switching away from experiments
      if (tab !== 'experiments') {
        setAssistantPanelProps(null);
        setIsDesignMode(false);
      }
      // Clear analyzer detail mode when switching tabs
      if (tab !== 'analyzer') {
        setAnalyzerInDetailMode(false);
        setAnalyzerBackToList(null);
      }
    },
    [onTabChange]
  );

  // Handle Decision Analyzer detail mode changes
  const handleAnalyzerDetailModeChange = useCallback(
    (inDetail: boolean, backToList: () => void) => {
      setAnalyzerInDetailMode(inDetail);
      setAnalyzerBackToList(() => backToList);
    },
    []
  );

  // Find active tab config for header
  const activeTabConfig = SIDEBAR_ITEMS.find((t) => t.id === activeTab);

  // Keyboard shortcut to toggle sidebar
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      setSidebarCollapsed((prev) => !prev);
    }
  }, []);

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  // Tab content component
  const renderTabContent = () => {
    // No tab selected (desktop landing) -> show the lightweight tool menu.
    // Mobile renders its own menu before ever calling this.
    if (!activeTab) {
      return <AdminOverview onSelect={handleTabChange} />;
    }
    return (
      <Suspense fallback={toolFallback}>
        {activeTab === 'users' && <UserManagement embedded />}
        {activeTab === 'personalities' && <PersonalityManager embedded />}
        {activeTab === 'analyzer' && (
          <DecisionAnalyzer
            embedded
            onDetailModeChange={handleAnalyzerDetailModeChange}
            onCaptureSelect={onCaptureSelect}
          />
        )}
        {activeTab === 'hand-replay' && <HandReplayBrowser />}
        {activeTab === 'playground' && <PromptPlayground embedded />}
        {activeTab === 'experiments' && (
          <ExperimentDesigner
            embedded
            onAssistantPanelChange={setAssistantPanelProps}
            onDesignModeChange={setIsDesignMode}
          />
        )}
        {activeTab === 'presets' && <PromptPresetManager embedded />}
        {activeTab === 'templates' && <TemplateEditor embedded />}
        {activeTab === 'settings' && (
          <UnifiedSettings
            embedded
            onCategoryChange={(category) => {
              window.history.replaceState(null, '', `/admin/settings/${category}`);
            }}
          />
        )}
        {activeTab === 'debug' && <DebugTools embedded />}
        {activeTab === 'chip-ledger' && <ChipLedgerPanel embedded />}
        {activeTab === 'whereabouts' && <CashWhereaboutsPanel embedded />}
        {activeTab === 'range-explorer' && <RangeExplorer embedded />}
        {activeTab === 'coach-metrics' && <CoachEffectivenessPanel embedded />}
      </Suspense>
    );
  };

  // Mobile layout - show menu if no tab selected, otherwise show content
  if (isMobile) {
    // No tab selected - show the menu using PageLayout (matches TournamentMenu style)
    if (!activeTab) {
      return (
        <>
          <MenuBar
            onBack={onBack}
            centerContent={<span className="admin-header-title">Admin Tools</span>}
            showUserInfo
            onAdminTools={() => {
              window.location.href = '/admin';
            }}
          />
          <PageLayout variant="top" glowColor="gold" hasMenuBar>
            <PageHeader title="Admin Tools" subtitle="Manage your poker game" />
            <div className="game-menu__options">
              {SIDEBAR_ITEMS.map((item) => (
                <button
                  key={item.id}
                  className="menu-option"
                  onClick={() => handleTabChange(item.id as AdminTab)}
                >
                  {item.icon}
                  <div className="option-content">
                    <h3>{item.label}</h3>
                    <p>{item.description}</p>
                  </div>
                  <ChevronRight className="option-arrow" size={20} />
                </button>
              ))}
            </div>
          </PageLayout>
        </>
      );
    }

    // Determine back button behavior
    // - If analyzer is in detail mode, go back to list
    // - Otherwise, go back to admin menu (clear activeTab)
    const handleMobileBack = () => {
      if (activeTab === 'analyzer' && analyzerInDetailMode && analyzerBackToList) {
        analyzerBackToList();
      } else {
        handleTabChange(undefined as unknown as AdminTab);
      }
    };

    // Tab selected - show content with back to admin menu
    return (
      <div className="admin-dashboard-layout admin-dashboard-layout--mobile">
        <AdminMenuContainer title={activeTabConfig?.label || 'Admin'} onBack={handleMobileBack}>
          <div className="admin-main__content admin-main__content--mobile">
            {renderTabContent()}
          </div>
        </AdminMenuContainer>
      </div>
    );
  }

  // Desktop layout - sidebar + main content + optional assistant panel
  return (
    <div
      className={`admin-dashboard-layout ${assistantPanelProps ? 'admin-dashboard-layout--with-assistant' : ''}`}
    >
      {/* Sidebar Navigation */}
      <AdminSidebar
        items={SIDEBAR_ITEMS}
        activeTab={activeTab}
        onTabChange={handleTabChange}
        collapsed={sidebarCollapsed}
        onCollapsedChange={setSidebarCollapsed}
      />

      {/* Main Content Area */}
      <main className="admin-main">
        {/* Content Header — shared breadcrumb + deterministic back-arrow */}
        <AdminHeader
          title={activeTabConfig?.label || 'Admin Tools'}
          subtitle={activeTabConfig?.description || 'Select a tool to get started'}
        />

        {/* Tab Content */}
        <div className="admin-main__content">{renderTabContent()}</div>
      </main>

      {/* Docked Assistant Panel (page-level) - only in design mode */}
      {activeTab === 'experiments' && isDesignMode && (
        <div className="admin-assistant-panel admin-assistant-panel--docked">
          <div className="admin-assistant-panel__header">
            <h3>
              <MessageSquare size={18} />
              Lab Assistant
            </h3>
          </div>
          {assistantPanelProps ? (
            <Suspense fallback={toolFallback}>
              <ExperimentChat {...assistantPanelProps} />
            </Suspense>
          ) : (
            <div style={{ padding: '1rem', color: '#888' }}>Loading chat...</div>
          )}
        </div>
      )}
    </div>
  );
}

export default AdminDashboard;
