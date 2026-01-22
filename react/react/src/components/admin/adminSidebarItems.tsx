import { Users, FlaskConical, Microscope, Beaker, FileText, Bug, Settings, BookMarked } from 'lucide-react';
import type { SidebarItem } from './AdminSidebar';

export const SIDEBAR_ITEMS: SidebarItem[] = [
  {
    id: 'personalities',
    label: 'Personalities',
    icon: <Users size={24} />,
    description: 'Create and customize AI opponents',
  },
  {
    id: 'analyzer',
    label: 'Decision Analyzer',
    icon: <Microscope size={24} />,
    description: 'Analyze and replay AI decision prompts',
  },
  {
    id: 'playground',
    label: 'Prompt Playground',
    icon: <FlaskConical size={24} />,
    description: 'View and replay any captured LLM prompt',
  },
  {
    id: 'experiments',
    label: 'Experiments',
    icon: <Beaker size={24} />,
    description: 'Design and run AI tournament experiments',
  },
  {
    id: 'presets',
    label: 'Presets',
    icon: <BookMarked size={24} />,
    description: 'Manage reusable prompt configurations',
  },
  {
    id: 'templates',
    label: 'Templates',
    icon: <FileText size={24} />,
    description: 'Edit system prompt templates',
  },
  {
    id: 'settings',
    label: 'Settings',
    icon: <Settings size={24} />,
    description: 'Models, capture, storage, and pricing',
  },
  {
    id: 'debug',
    label: 'Debug',
    icon: <Bug size={24} />,
    description: 'Inspect game state and AI internals',
  },
];
