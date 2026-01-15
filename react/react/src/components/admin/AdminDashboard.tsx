import { useState } from 'react';
import { Users, FlaskConical, Microscope } from 'lucide-react';
import { PageLayout, PageHeader } from '../shared';
import { PersonalityManager } from './PersonalityManager';
import { DecisionAnalyzer } from './DecisionAnalyzer';
import { PromptPlayground } from '../debug/PromptPlayground';
import './AdminDashboard.css';

type AdminTab = 'personalities' | 'analyzer' | 'playground';

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
];

interface AdminDashboardProps {
  onBack: () => void;
}

export function AdminDashboard({ onBack }: AdminDashboardProps) {
  const [activeTab, setActiveTab] = useState<AdminTab>('personalities');

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
            onClick={() => setActiveTab(tab.id)}
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
      </div>
    </PageLayout>
  );
}

export default AdminDashboard;
