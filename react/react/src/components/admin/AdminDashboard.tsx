import { useState, useEffect, useCallback } from 'react';
import { Users, FlaskConical, Microscope, Sliders, DollarSign, FileText, Bug, Settings } from 'lucide-react';
import { PageLayout, PageHeader } from '../shared';
import { PersonalityManager } from './PersonalityManager';
import { DecisionAnalyzer } from './DecisionAnalyzer';
import { PromptPlayground } from '../debug/PromptPlayground';
import { ModelManager } from './ModelManager';
import { PricingManager } from './PricingManager';
import { TemplateEditor } from './TemplateEditor';
import { DebugTools } from './DebugTools';
import { CaptureSettings } from './CaptureSettings';
import './AdminDashboard.css';

type AdminTab = 'personalities' | 'analyzer' | 'playground' | 'models' | 'pricing' | 'templates' | 'settings' | 'debug';

interface TabConfig {
  id: AdminTab;
  label: string;
  icon: React.ReactNode;
  description: string;
}

const TABS: TabConfig[] = [
  {
    id: 'personalities',
    label: 'Personalities',
    icon: <Users size={18} />,
    description: 'Create and customize AI opponents',
  },
  {
    id: 'analyzer',
    label: 'Decision Analyzer',
    icon: <Microscope size={18} />,
    description: 'Analyze and replay AI decision prompts',
  },
  {
    id: 'playground',
    label: 'Prompt Playground',
    icon: <FlaskConical size={18} />,
    description: 'View and replay any captured LLM prompt',
  },
  {
    id: 'models',
    label: 'Models',
    icon: <Sliders size={18} />,
    description: 'Enable or disable LLM models by provider',
  },
  {
    id: 'pricing',
    label: 'Pricing',
    icon: <DollarSign size={18} />,
    description: 'View and manage LLM pricing configuration',
  },
  {
    id: 'templates',
    label: 'Templates',
    icon: <FileText size={18} />,
    description: 'Edit system prompt templates',
  },
  {
    id: 'settings',
    label: 'Settings',
    icon: <Settings size={18} />,
    description: 'Configure prompt capture and other settings',
  },
  {
    id: 'debug',
    label: 'Debug',
    icon: <Bug size={18} />,
    description: 'Inspect game state and AI internals',
  },
];

interface AdminDashboardProps {
  onBack: () => void;
}

// Get initial tab from URL or default to 'personalities'
function getInitialTab(): AdminTab {
  const urlParams = new URLSearchParams(window.location.search);
  const tabParam = urlParams.get('tab');
  const validTabs = TABS.map(t => t.id);
  if (tabParam && validTabs.includes(tabParam as AdminTab)) {
    return tabParam as AdminTab;
  }
  return 'personalities';
}

// Update URL with current tab without page reload
function updateUrlTab(tab: AdminTab) {
  const url = new URL(window.location.href);
  url.searchParams.set('view', 'admin');
  url.searchParams.set('tab', tab);
  window.history.replaceState({}, '', url.toString());
}

export function AdminDashboard({ onBack }: AdminDashboardProps) {
  const [activeTab, setActiveTab] = useState<AdminTab>(getInitialTab);

  // Update URL when tab changes
  const handleTabChange = useCallback((tab: AdminTab) => {
    setActiveTab(tab);
    updateUrlTab(tab);
  }, []);

  // Set initial URL on mount
  useEffect(() => {
    updateUrlTab(activeTab);
  }, []);

  // Find active tab config for subtitle
  const activeTabConfig = TABS.find(t => t.id === activeTab);

  return (
    <PageLayout variant="top" glowColor="gold" maxWidth="xl">
      <PageHeader
        title="Admin Dashboard"
        subtitle={activeTabConfig?.description || 'Manage your poker game'}
        onBack={onBack}
        titleVariant="primary"
      />

      {/* Tab Navigation */}
      <div className="admin-tabs">
        {TABS.map(tab => (
          <button
            key={tab.id}
            className={`admin-tab ${activeTab === tab.id ? 'admin-tab--active' : ''}`}
            onClick={() => handleTabChange(tab.id)}
            type="button"
          >
            <span className="admin-tab__icon">{tab.icon}</span>
            <span className="admin-tab__label">{tab.label}</span>
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="admin-content">
        {activeTab === 'personalities' && (
          <PersonalityManager embedded />
        )}
        {activeTab === 'analyzer' && (
          <DecisionAnalyzer embedded />
        )}
        {activeTab === 'playground' && (
          <PromptPlayground embedded />
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
    </PageLayout>
  );
}

export default AdminDashboard;
