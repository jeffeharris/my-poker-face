import { useState } from 'react';
import {
  Users,
  FlaskConical,
  Microscope,
  Beaker,
  Sliders,
  DollarSign,
  FileText,
  Bug,
  Settings,
} from 'lucide-react';
import { AdminLayout, type AdminNavItem } from './AdminLayout';
import { PersonalityManager } from './PersonalityManager';
import { DecisionAnalyzer } from './DecisionAnalyzer';
import { PromptPlayground } from '../debug/PromptPlayground';
import { ExperimentDesigner } from './ExperimentDesigner';
import { ModelManager } from './ModelManager';
import { PricingManager } from './PricingManager';
import { TemplateEditor } from './TemplateEditor';
import { DebugTools } from './DebugTools';
import { CaptureSettings } from './CaptureSettings';

type AdminTab =
  | 'personalities'
  | 'analyzer'
  | 'playground'
  | 'experiments'
  | 'models'
  | 'pricing'
  | 'templates'
  | 'settings'
  | 'debug';

const NAV_ITEMS: AdminNavItem[] = [
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
    id: 'experiments',
    label: 'Experiments',
    icon: <Beaker size={18} />,
    description: 'Design and run AI tournament experiments',
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

export function AdminDashboard({ onBack }: AdminDashboardProps) {
  const [activeTab, setActiveTab] = useState<AdminTab>('personalities');

  const renderContent = () => {
    switch (activeTab) {
      case 'personalities':
        return <PersonalityManager embedded />;
      case 'analyzer':
        return <DecisionAnalyzer embedded />;
      case 'playground':
        return <PromptPlayground embedded />;
      case 'experiments':
        return <ExperimentDesigner embedded />;
      case 'models':
        return <ModelManager embedded />;
      case 'pricing':
        return <PricingManager embedded />;
      case 'templates':
        return <TemplateEditor embedded />;
      case 'settings':
        return <CaptureSettings embedded />;
      case 'debug':
        return <DebugTools embedded />;
      default:
        return null;
    }
  };

  return (
    <AdminLayout
      navItems={NAV_ITEMS}
      activeItem={activeTab}
      onNavChange={(id) => setActiveTab(id as AdminTab)}
      title="Admin Dashboard"
      subtitle="Manage your poker game"
      onBack={onBack}
    >
      {renderContent()}
    </AdminLayout>
  );
}

export default AdminDashboard;
