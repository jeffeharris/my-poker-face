import { useState, useEffect, useCallback } from 'react';
import { Sliders, Database, HardDrive, DollarSign, Menu, Check, Palette, Bell } from 'lucide-react';
import { useViewport } from '../../../hooks/useViewport';
import { MobileFilterSheet } from '../shared/MobileFilterSheet';
import type { AlertState, CategoryConfig, SettingsCategory, UnifiedSettingsProps } from './types';
import { ModelsSection } from './sections/ModelsSection';
import { CaptureSection } from './sections/CaptureSection';
import { StorageSection } from './sections/StorageSection';
import { AlertingSection } from './sections/AlertingSection';
import { PricingSection } from './sections/PricingSection';
import { AppearanceSection } from './sections/AppearanceSection';
import '../AdminShared.css';
import './UnifiedSettings.css';

// ============================================
// Category Configuration
// ============================================

const CATEGORIES: CategoryConfig[] = [
  {
    id: 'models',
    label: 'Models',
    description: 'Model defaults and availability',
    icon: <Sliders size={20} />,
  },
  {
    id: 'capture',
    label: 'Capture',
    description: 'Prompt capture settings',
    icon: <Database size={20} />,
  },
  {
    id: 'storage',
    label: 'Storage',
    description: 'Database size breakdown',
    icon: <HardDrive size={20} />,
  },
  {
    id: 'pricing',
    label: 'Pricing',
    description: 'Model pricing config',
    icon: <DollarSign size={20} />,
  },
  {
    id: 'appearance',
    label: 'Appearance',
    description: 'Card deck and display',
    icon: <Palette size={20} />,
  },
  {
    id: 'alerting',
    label: 'Alerting',
    description: 'Webhook for error/ledger/budget alerts',
    icon: <Bell size={20} />,
  },
];

// ============================================
// Main Component (orchestrator)
// ============================================

export function UnifiedSettings({
  embedded = false,
  initialCategory,
  onCategoryChange,
}: UnifiedSettingsProps) {
  const { isDesktop, isMobile } = useViewport();
  const [activeCategory, setActiveCategory] = useState<SettingsCategory>(
    initialCategory || 'models'
  );
  const [masterPanelOpen, setMasterPanelOpen] = useState(false);
  const [categorySheetOpen, setCategorySheetOpen] = useState(false);
  const [alert, setAlert] = useState<AlertState | null>(null);

  // Sync activeCategory when initialCategory prop changes (URL navigation)
  useEffect(() => {
    if (initialCategory) {
      setActiveCategory(initialCategory);
    }
  }, [initialCategory]);

  // Unified category change handler — updates local state + notifies parent for URL sync
  const handleCategoryChange = useCallback(
    (category: SettingsCategory) => {
      setActiveCategory(category);
      onCategoryChange?.(category);
    },
    [onCategoryChange]
  );

  // Auto-dismiss alerts
  useEffect(() => {
    if (alert) {
      const timer = setTimeout(() => setAlert(null), 4000);
      return () => clearTimeout(timer);
    }
  }, [alert]);

  const showAlert = useCallback((type: AlertState['type'], message: string) => {
    setAlert({ type, message });
  }, []);

  const activeCategoryConfig = CATEGORIES.find((c) => c.id === activeCategory);

  const renderContent = () => {
    switch (activeCategory) {
      case 'models':
        return <ModelsSection showAlert={showAlert} />;
      case 'capture':
        return <CaptureSection showAlert={showAlert} />;
      case 'storage':
        return <StorageSection showAlert={showAlert} />;
      case 'pricing':
        return <PricingSection />;
      case 'appearance':
        return <AppearanceSection />;
      case 'alerting':
        return <AlertingSection showAlert={showAlert} />;
      default:
        return null;
    }
  };

  return (
    <div className={`admin-master-detail ${embedded ? '' : 'us-standalone'}`}>
      {/* Alert Toast */}
      {alert && (
        <div className="admin-toast-container">
          <div className={`admin-alert admin-alert--${alert.type}`}>
            <span className="admin-alert__icon">
              {alert.type === 'success' && '✓'}
              {alert.type === 'error' && '✕'}
              {alert.type === 'info' && 'i'}
            </span>
            <span className="admin-alert__content">{alert.message}</span>
            <button className="admin-alert__dismiss" onClick={() => setAlert(null)}>
              ×
            </button>
          </div>
        </div>
      )}

      {/* Master Panel - Category List (tablet/desktop only) */}
      <aside className={`admin-master ${masterPanelOpen || isDesktop ? 'admin-master--open' : ''}`}>
        <div className="admin-master__header">
          <h3 className="admin-master__title">Settings</h3>
        </div>
        <div className="admin-master__list">
          {CATEGORIES.map((category) => (
            <button
              key={category.id}
              type="button"
              className={`admin-master__item ${activeCategory === category.id ? 'admin-master__item--selected' : ''}`}
              onClick={() => {
                handleCategoryChange(category.id);
                if (!isDesktop) setMasterPanelOpen(false);
              }}
            >
              <span className="admin-master__item-icon">{category.icon}</span>
              <span className="admin-master__item-name">{category.label}</span>
            </button>
          ))}
        </div>
      </aside>

      {/* Detail Panel */}
      <main className="admin-detail">
        {/* Mobile: category selector button + bottom sheet */}
        {isMobile && (
          <>
            <button
              type="button"
              className="us-category-trigger"
              onClick={() => setCategorySheetOpen(true)}
            >
              <span className="us-category-trigger__icon">{activeCategoryConfig?.icon}</span>
              <span className="us-category-trigger__label">{activeCategoryConfig?.label}</span>
              <Menu size={18} />
            </button>

            <MobileFilterSheet
              isOpen={categorySheetOpen}
              onClose={() => setCategorySheetOpen(false)}
              title="Settings"
            >
              <div className="us-category-sheet__list">
                {CATEGORIES.map((category) => (
                  <button
                    key={category.id}
                    type="button"
                    className={`us-category-sheet__item ${activeCategory === category.id ? 'us-category-sheet__item--active' : ''}`}
                    onClick={() => {
                      handleCategoryChange(category.id);
                      setCategorySheetOpen(false);
                    }}
                  >
                    <span className="us-category-sheet__item-icon">{category.icon}</span>
                    <div className="us-category-sheet__item-text">
                      <span className="us-category-sheet__item-label">{category.label}</span>
                      <span className="us-category-sheet__item-desc">{category.description}</span>
                    </div>
                    {activeCategory === category.id && (
                      <Check size={18} className="us-category-sheet__item-check" />
                    )}
                  </button>
                ))}
              </div>
            </MobileFilterSheet>
          </>
        )}

        {/* Tablet: slide-out toggle */}
        {!isMobile && !isDesktop && (
          <button
            type="button"
            className="admin-master-toggle"
            onClick={() => setMasterPanelOpen(!masterPanelOpen)}
          >
            <Menu size={20} />
            <span>{activeCategoryConfig?.label || 'Settings'}</span>
          </button>
        )}

        {!isMobile && (
          <div className="admin-detail__header">
            <div>
              <h2 className="admin-detail__title">{activeCategoryConfig?.label}</h2>
              <p className="admin-detail__subtitle">{activeCategoryConfig?.description}</p>
            </div>
          </div>
        )}

        <div className="admin-detail__content">{renderContent()}</div>
      </main>

      {/* Backdrop for tablet sidebar */}
      {!isMobile && !isDesktop && masterPanelOpen && (
        <div className="us-backdrop" onClick={() => setMasterPanelOpen(false)} />
      )}
    </div>
  );
}

export default UnifiedSettings;
