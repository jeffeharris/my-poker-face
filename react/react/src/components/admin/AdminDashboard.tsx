import { useState, useEffect, useCallback } from 'react';
import { ArrowLeft, ChevronRight, MessageSquare } from 'lucide-react';
import { AdminSidebar, type AdminTab } from './AdminSidebar';
import { PersonalityManager } from './PersonalityManager';
import { DecisionAnalyzer } from './DecisionAnalyzer';
import { PromptPlayground } from '../debug/PromptPlayground';
import { ExperimentDesigner, ExperimentChat, type AssistantPanelProps } from './ExperimentDesigner';
import { PromptPresetManager } from './PromptPresetManager';
import { TemplateEditor } from './TemplateEditor';
import { DebugTools } from './DebugTools';
import { UnifiedSettings } from './UnifiedSettings';
import { AdminMenuContainer } from './AdminMenuContainer';
import { PageLayout, PageHeader } from '../shared';
import { useViewport } from '../../hooks/useViewport';
import { SIDEBAR_ITEMS } from './adminSidebarItems';
import './AdminDashboard.css';
import '../menus/GameMenu.css';

interface AdminDashboardProps {
  onBack: () => void;
  initialTab?: AdminTab;
  onTabChange?: (tab: AdminTab) => void;
}

export function AdminDashboard({ onBack, initialTab, onTabChange }: AdminDashboardProps) {
  const { isMobile } = useViewport();
  const [activeTab, setActiveTab] = useState<AdminTab | undefined>(initialTab);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [assistantPanelProps, setAssistantPanelProps] = useState<AssistantPanelProps | null>(null);
  const [isDesignMode, setIsDesignMode] = useState(false);

  // Sync activeTab with initialTab when it changes (URL navigation)
  useEffect(() => {
    setActiveTab(initialTab);
  }, [initialTab]);

  // Wrapper to update both state and notify parent
  const handleTabChange = useCallback((tab: AdminTab) => {
    setActiveTab(tab);
    onTabChange?.(tab);
    // Clear assistant panel when switching away from experiments
    if (tab !== 'experiments') {
      setAssistantPanelProps(null);
      setIsDesignMode(false);
    }
  }, [onTabChange]);

  // Find active tab config for header
  const activeTabConfig = SIDEBAR_ITEMS.find(t => t.id === activeTab);

  // Keyboard shortcut to toggle sidebar
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      setSidebarCollapsed(prev => !prev);
    }
  }, []);

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  // Tab content component
  const renderTabContent = () => (
    <>
      {activeTab === 'personalities' && (
        <PersonalityManager embedded />
      )}
      {activeTab === 'analyzer' && (
        <DecisionAnalyzer embedded />
      )}
      {activeTab === 'playground' && (
        <PromptPlayground embedded />
      )}
      {activeTab === 'experiments' && (
        <ExperimentDesigner
          embedded
          onAssistantPanelChange={setAssistantPanelProps}
          onDesignModeChange={setIsDesignMode}
        />
      )}
      {activeTab === 'presets' && (
        <PromptPresetManager embedded />
      )}
      {activeTab === 'templates' && (
        <TemplateEditor embedded />
      )}
      {activeTab === 'settings' && (
        <UnifiedSettings embedded />
      )}
      {activeTab === 'debug' && (
        <DebugTools embedded />
      )}
    </>
  );

  // Mobile layout - show menu if no tab selected, otherwise show content
  if (isMobile) {
    // No tab selected - show the menu using PageLayout (matches GameMenu style)
    if (!activeTab) {
      return (
        <PageLayout variant="top" glowColor="gold">
          <PageHeader
            title="Admin Tools"
            subtitle="Manage your poker game"
            onBack={onBack}
          />
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
      );
    }

    // Tab selected - show content with back to menu
    return (
      <div className="admin-dashboard-layout admin-dashboard-layout--mobile">
        <AdminMenuContainer
          title={activeTabConfig?.label || 'Admin'}
          subtitle={activeTabConfig?.description}
          onBack={onBack}
          navItems={SIDEBAR_ITEMS}
          activeNavId={activeTab}
          onNavChange={(id) => handleTabChange(id as AdminTab)}
        >
          <div className="admin-main__content admin-main__content--mobile">
            {renderTabContent()}
          </div>
        </AdminMenuContainer>
      </div>
    );
  }

  // Desktop layout - sidebar + main content + optional assistant panel
  return (
    <div className={`admin-dashboard-layout ${assistantPanelProps ? 'admin-dashboard-layout--with-assistant' : ''}`}>
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
        {/* Content Header */}
        <header className="admin-main__header">
          <button
            className="admin-main__back"
            onClick={onBack}
            aria-label="Go back"
          >
            <ArrowLeft size={20} />
          </button>
          <div className="admin-main__header-text">
            <h1 className="admin-main__title">{activeTabConfig?.label || 'Admin'}</h1>
            <p className="admin-main__subtitle">{activeTabConfig?.description}</p>
          </div>
        </header>

        {/* Tab Content */}
        <div className="admin-main__content">
          {renderTabContent()}
        </div>
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
            <ExperimentChat {...assistantPanelProps} />
          ) : (
            <div style={{ padding: '1rem', color: '#888' }}>
              Loading chat...
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default AdminDashboard;
