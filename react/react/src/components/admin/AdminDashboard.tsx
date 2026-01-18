import { useState, useEffect, useCallback } from 'react';
import { Users, FlaskConical, Microscope, Beaker, Sliders, DollarSign, FileText, Bug, Settings, ArrowLeft } from 'lucide-react';
import { AdminSidebar, type AdminTab, type SidebarItem } from './AdminSidebar';
import { PersonalityManager } from './PersonalityManager';
import { DecisionAnalyzer } from './DecisionAnalyzer';
import { PromptPlayground } from '../debug/PromptPlayground';
import { ExperimentDesigner } from './ExperimentDesigner';
import { ModelManager } from './ModelManager';
import { PricingManager } from './PricingManager';
import { TemplateEditor } from './TemplateEditor';
import { DebugTools } from './DebugTools';
import { CaptureSettings } from './CaptureSettings';
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
    id: 'models',
    label: 'Models',
    icon: <Sliders size={20} />,
    description: 'Enable or disable LLM models by provider',
  },
  {
    id: 'pricing',
    label: 'Pricing',
    icon: <DollarSign size={20} />,
    description: 'View and manage LLM pricing configuration',
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
    description: 'Configure prompt capture and other settings',
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
}

export function AdminDashboard({ onBack }: AdminDashboardProps) {
  const [activeTab, setActiveTab] = useState<AdminTab>('personalities');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

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

  return (
    <div className="admin-dashboard">
      {/* Sidebar Navigation */}
      <AdminSidebar
        items={SIDEBAR_ITEMS}
        activeTab={activeTab}
        onTabChange={setActiveTab}
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
          {activeTab === 'models' && (
            <ModelManager embedded />
          )}
          {activeTab === 'pricing' && (
            <PricingManager embedded />
          )}
          {activeTab === 'templates' && (
            <TemplateEditor embedded />
          )}
          {activeTab === 'settings' && (
            <CaptureSettings embedded />
          )}
          {activeTab === 'debug' && (
            <DebugTools embedded />
          )}
        </div>
      </main>
    </div>
  );
}

export default AdminDashboard;
