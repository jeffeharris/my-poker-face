import { useState, useEffect, useCallback } from 'react';
import { Users, FlaskConical, Microscope, Beaker, FileText, Bug, Settings, ArrowLeft } from 'lucide-react';
import { AdminSidebar, type AdminTab, type SidebarItem } from './AdminSidebar';
import { PersonalityManager } from './PersonalityManager';
import { DecisionAnalyzer } from './DecisionAnalyzer';
import { PromptPlayground } from '../debug/PromptPlayground';
import { ExperimentDesigner } from './ExperimentDesigner';
import { TemplateEditor } from './TemplateEditor';
import { DebugTools } from './DebugTools';
import { UnifiedSettings } from './UnifiedSettings';
import { AdminMenuContainer } from './AdminMenuContainer';
import { useViewport } from '../../hooks/useViewport';
import './AdminDashboard.css';

const SIDEBAR_ITEMS: SidebarItem[] = [
  {
    id: 'personalities',
    label: 'Personalities',
    icon: <Users size={20} />,
    description: 'Create and customize AI opponents',
  },
  {
    id: 'analyzer',
    label: 'Decision Analyzer',
    icon: <Microscope size={20} />,
    description: 'Analyze and replay AI decision prompts',
  },
  {
    id: 'playground',
    label: 'Prompt Playground',
    icon: <FlaskConical size={20} />,
    description: 'View and replay any captured LLM prompt',
  },
  {
    id: 'experiments',
    label: 'Experiments',
    icon: <Beaker size={20} />,
    description: 'Design and run AI tournament experiments',
  },
  {
    id: 'templates',
    label: 'Templates',
    icon: <FileText size={20} />,
    description: 'Edit system prompt templates',
  },
  {
    id: 'settings',
    label: 'Settings',
    icon: <Settings size={20} />,
    description: 'Models, capture, storage, and pricing',
  },
  {
    id: 'debug',
    label: 'Debug',
    icon: <Bug size={20} />,
    description: 'Inspect game state and AI internals',
  },
];

interface AdminDashboardProps {
  onBack: () => void;
  initialTab?: AdminTab;
  onTabChange?: (tab: AdminTab) => void;
}

export function AdminDashboard({ onBack, initialTab = 'personalities', onTabChange }: AdminDashboardProps) {
  const { isMobile } = useViewport();
  const [activeTab, setActiveTab] = useState<AdminTab>(initialTab);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  // Sync activeTab with initialTab when it changes (URL navigation)
  useEffect(() => {
    setActiveTab(initialTab);
  }, [initialTab]);

  // Wrapper to update both state and notify parent
  const handleTabChange = useCallback((tab: AdminTab) => {
    setActiveTab(tab);
    onTabChange?.(tab);
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
        <ExperimentDesigner embedded />
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

  // Mobile layout - full screen with AdminMenuContainer
  if (isMobile) {
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

  // Desktop layout - sidebar + main content
  return (
    <div className="admin-dashboard-layout">
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
    </div>
  );
}

export default AdminDashboard;
