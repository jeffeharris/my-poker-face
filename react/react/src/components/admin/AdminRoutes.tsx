import { useState, useEffect, useCallback, lazy, Suspense } from 'react';
import { Routes, Route, Navigate, useParams, useNavigate } from 'react-router-dom';
import { MessageSquare, X, Send, Loader2, Columns2, Maximize2 } from 'lucide-react';
import { AdminDashboard } from './AdminDashboard';
import { AdminToolLayout } from './AdminToolLayout';
import { getAdminOrigin } from './adminOrigin';
import type { AssistantPanelProps } from './ExperimentDesigner';
import type { SettingsCategory } from './UnifiedSettings';
import { useViewport } from '../../hooks/useViewport';
import { useAuth, hasPermission } from '../../hooks/useAuth';
import { config } from '../../config';
import { logger } from '../../utils/logger';
import type { AdminTab } from './AdminSidebar';
import './AdminShared.css';

// Heavy admin tools are lazy-loaded so they don't bloat the main AdminRoutes
// chunk. Each is a NAMED export, so map it onto `default` for React.lazy.
const ExperimentDesigner = lazy(() =>
  import('./ExperimentDesigner/ExperimentDesigner').then((m) => ({ default: m.ExperimentDesigner }))
);
const ExperimentChat = lazy(() =>
  import('./ExperimentDesigner/ExperimentChat').then((m) => ({ default: m.ExperimentChat }))
);
const ExperimentDetail = lazy(() =>
  import('./ExperimentDesigner/ExperimentDetail').then((m) => ({ default: m.ExperimentDetail }))
);
const ReplayResults = lazy(() =>
  import('./ReplayResults').then((m) => ({ default: m.ReplayResults }))
);
const DecisionAnalyzer = lazy(() =>
  import('./DecisionAnalyzer').then((m) => ({ default: m.DecisionAnalyzer }))
);
const UnifiedSettings = lazy(() =>
  import('./UnifiedSettings').then((m) => ({ default: m.UnifiedSettings }))
);

// Local Suspense fallback for the inline ExperimentChat panel (the tool
// layout has its own copy for the main content area).
const toolFallback = (
  <div style={{ padding: '2rem', textAlign: 'center', color: 'rgba(255,255,255,0.5)' }}>
    Loading…
  </div>
);

const VALID_TABS: AdminTab[] = [
  'users',
  'personalities',
  'analyzer',
  'hand-replay',
  'playground',
  'experiments',
  'presets',
  'templates',
  'settings',
  'debug',
  'chip-ledger',
  'whereabouts',
  'range-explorer',
  'archetype-review',
  'coach-metrics',
];

/**
 * Shared hook for capture selection with URL updates.
 * Updates the URL without triggering React Router navigation/remount.
 */
function useCaptureSelectHandler() {
  return useCallback((captureId: number | null) => {
    const newPath = captureId ? `/admin/analyzer/${captureId}` : '/admin/analyzer';
    window.history.replaceState(null, '', newPath);
  }, []);
}

/**
 * Wrapper for experiment detail view with URL params
 */
function ExperimentDetailWrapper() {
  const { experimentId } = useParams<{ experimentId: string }>();
  const navigate = useNavigate();
  const { isMobile } = useViewport();

  // Page-level assistant state
  const [showAssistant, setShowAssistant] = useState(false);
  const [assistantMode, setAssistantMode] = useState<'overlay' | 'docked'>(() => {
    const saved = localStorage.getItem('experimentAssistantMode');
    return saved === 'overlay' || saved === 'docked' ? saved : 'docked';
  });
  const [chatMessages, setChatMessages] = useState<
    Array<{ role: 'user' | 'assistant'; content: string }>
  >([]);
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);

  const experimentIdNum = experimentId ? parseInt(experimentId, 10) : NaN;

  // Load chat history when opening assistant
  const handleOpenAssistant = async () => {
    setShowAssistant(true);
    if (chatMessages.length === 0 && !isNaN(experimentIdNum)) {
      try {
        const response = await fetch(
          `${config.API_URL}/api/experiments/${experimentIdNum}/chat/history`
        );
        const data = await response.json();
        if (data.success && data.history) {
          setChatMessages(data.history);
        }
      } catch (err) {
        logger.error('Failed to load chat history:', err);
      }
    }
  };

  const handleSendMessage = async () => {
    if (!chatInput.trim() || chatLoading || isNaN(experimentIdNum)) return;

    const message = chatInput.trim();
    setChatInput('');
    setChatMessages((prev) => [...prev, { role: 'user', content: message }]);
    setChatLoading(true);

    try {
      const response = await fetch(`${config.API_URL}/api/experiments/${experimentIdNum}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message }),
      });
      const data = await response.json();
      if (data.success) {
        setChatMessages((prev) => [...prev, { role: 'assistant', content: data.response }]);
      } else {
        setChatMessages((prev) => [
          ...prev,
          { role: 'assistant', content: `Error: ${data.error}` },
        ]);
      }
    } catch {
      setChatMessages((prev) => [
        ...prev,
        { role: 'assistant', content: 'Error: Failed to connect to server' },
      ]);
    } finally {
      setChatLoading(false);
    }
  };

  const handleClearChat = async () => {
    if (isNaN(experimentIdNum)) return;
    try {
      await fetch(`${config.API_URL}/api/experiments/${experimentIdNum}/chat/clear`, {
        method: 'POST',
      });
      setChatMessages([]);
    } catch (err) {
      logger.error('Failed to clear chat history:', err);
    }
  };

  const handleToggleMode = () => {
    const newMode = assistantMode === 'overlay' ? 'docked' : 'overlay';
    setAssistantMode(newMode);
    localStorage.setItem('experimentAssistantMode', newMode);
  };

  // ESC key handler
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && showAssistant && assistantMode === 'overlay') {
        setShowAssistant(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [showAssistant, assistantMode]);

  const handleBack = () => {
    navigate('/admin/experiments');
  };

  const handleEditInLabAssistant = (
    experiment: Parameters<
      NonNullable<React.ComponentProps<typeof ExperimentDetail>['onEditInLabAssistant']>
    >[0]
  ) => {
    navigate('/admin/experiments', { state: { editExperiment: experiment } });
  };

  const handleBuildFromSuggestion = (
    experiment: Parameters<
      NonNullable<React.ComponentProps<typeof ExperimentDetail>['onBuildFromSuggestion']>
    >[0],
    suggestion: Parameters<
      NonNullable<React.ComponentProps<typeof ExperimentDetail>['onBuildFromSuggestion']>
    >[1]
  ) => {
    navigate('/admin/experiments', { state: { buildFromSuggestion: { experiment, suggestion } } });
  };

  if (!experimentId || isNaN(experimentIdNum)) {
    return <Navigate to="/admin/experiments" replace />;
  }

  const isDockedMode = assistantMode === 'docked';
  const showDockedAssistant = isDockedMode && showAssistant;

  // Assistant Panel component (shared between overlay and docked)
  const AssistantPanel = ({ isDocked }: { isDocked: boolean }) => (
    <div className={`admin-assistant-panel ${isDocked ? 'admin-assistant-panel--docked' : ''}`}>
      <div className="admin-assistant-panel__header">
        <h3>
          <MessageSquare size={18} />
          Experiment Assistant
        </h3>
        <div className="admin-assistant-panel__header-actions">
          <button
            type="button"
            className="admin-assistant-panel__mode-btn"
            onClick={handleToggleMode}
            title={isDocked ? 'Switch to overlay mode' : 'Switch to docked mode'}
          >
            {isDocked ? <Maximize2 size={16} /> : <Columns2 size={16} />}
          </button>
          {chatMessages.length > 0 && (
            <button
              type="button"
              className="admin-assistant-panel__clear-btn"
              onClick={handleClearChat}
              title="Clear chat history"
            >
              Clear
            </button>
          )}
          <button
            type="button"
            className="admin-assistant-panel__close-btn"
            onClick={() => setShowAssistant(false)}
          >
            <X size={18} />
          </button>
        </div>
      </div>
      <div className="admin-assistant-panel__messages">
        {chatMessages.length === 0 && (
          <div className="admin-assistant-panel__welcome">
            <p>Ask me anything about this experiment:</p>
            <ul>
              <li>Why were certain configurations chosen?</li>
              <li>What do the results mean?</li>
              <li>How do the variants compare?</li>
              <li>What follow-up experiments should I run?</li>
            </ul>
          </div>
        )}
        {chatMessages.map((msg, idx) => (
          <div
            key={idx}
            className={`admin-assistant-panel__message admin-assistant-panel__message--${msg.role}`}
          >
            {msg.content.split('\n').map((line, i) => (
              <p key={i}>{line || '\u00A0'}</p>
            ))}
          </div>
        ))}
        {chatLoading && (
          <div className="admin-assistant-panel__message admin-assistant-panel__message--assistant">
            <Loader2 size={16} className="animate-spin" />
            <span>Thinking...</span>
          </div>
        )}
      </div>
      <form
        className="admin-assistant-panel__input-area"
        onSubmit={(e) => {
          e.preventDefault();
          handleSendMessage();
        }}
      >
        <input
          type="text"
          className="admin-assistant-panel__input"
          value={chatInput}
          onChange={(e) => setChatInput(e.target.value)}
          placeholder="Ask about this experiment..."
          disabled={chatLoading}
        />
        <button
          type="submit"
          className="admin-assistant-panel__send-btn"
          disabled={!chatInput.trim() || chatLoading}
        >
          <Send size={16} />
        </button>
      </form>
    </div>
  );

  // Docked panel is desktop-only; mobile always uses the full-screen overlay.
  const dockedAssistant =
    !isMobile && showDockedAssistant ? <AssistantPanel isDocked={true} /> : undefined;
  const overlayAssistant =
    showAssistant && (isMobile || !isDockedMode) ? (
      <div className="admin-assistant-overlay" onClick={() => setShowAssistant(false)}>
        <div onClick={(e) => e.stopPropagation()}>
          <AssistantPanel isDocked={false} />
        </div>
      </div>
    ) : undefined;

  return (
    <AdminToolLayout
      activeTab="experiments"
      title="Experiment Details"
      subtitle="View experiment results and analysis"
      assistant={dockedAssistant}
      overlay={overlayAssistant}
    >
      <ExperimentDetail
        experimentId={experimentIdNum}
        onBack={handleBack}
        onEditInLabAssistant={handleEditInLabAssistant}
        onBuildFromSuggestion={handleBuildFromSuggestion}
        onOpenAssistant={handleOpenAssistant}
      />
    </AdminToolLayout>
  );
}

/**
 * Wrapper for replay experiment results view with URL params
 */
function ReplayResultsWrapper() {
  const { experimentId } = useParams<{ experimentId: string }>();
  const navigate = useNavigate();

  const experimentIdNum = experimentId ? parseInt(experimentId, 10) : NaN;

  const handleBack = () => {
    navigate('/admin/experiments');
  };

  if (!experimentId || isNaN(experimentIdNum)) {
    return <Navigate to="/admin/experiments" replace />;
  }

  return (
    <AdminToolLayout
      activeTab="experiments"
      title="Replay Results"
      subtitle="View replay experiment results and analysis"
    >
      <ReplayResults experimentId={experimentIdNum} onBack={handleBack} />
    </AdminToolLayout>
  );
}

/**
 * Wrapper for new experiment design page (/admin/experiments/new)
 */
function NewExperimentWrapper() {
  const [assistantPanelProps, setAssistantPanelProps] = useState<AssistantPanelProps | null>(null);

  // Docked Lab Assistant panel (desktop only — AdminToolLayout ignores it on
  // mobile, where ExperimentDesigner manages its own assistant).
  const assistant = assistantPanelProps ? (
    <div className="admin-assistant-panel admin-assistant-panel--docked">
      <div className="admin-assistant-panel__header">
        <h3>
          <MessageSquare size={18} />
          Lab Assistant
        </h3>
      </div>
      <Suspense fallback={toolFallback}>
        <ExperimentChat {...assistantPanelProps} />
      </Suspense>
    </div>
  ) : undefined;

  return (
    <AdminToolLayout
      activeTab="experiments"
      title="New Experiment"
      subtitle="Design a new experiment with the Lab Assistant"
      assistant={assistant}
    >
      <ExperimentDesigner
        embedded
        initialMode="design"
        onAssistantPanelChange={setAssistantPanelProps}
      />
    </AdminToolLayout>
  );
}

/**
 * Wrapper for Decision Analyzer with URL-based capture selection
 */
function DecisionAnalyzerWrapper() {
  const { captureId } = useParams<{ captureId: string }>();
  const captureIdNum = captureId ? parseInt(captureId, 10) : undefined;

  // The analyzer updates the URL via history.replaceState (no remount), which
  // useParams/useLocation never observe — so the breadcrumb leaf must track
  // the live selection in state, not the (stale) route param.
  const [selectedCapture, setSelectedCapture] = useState<number | undefined>(captureIdNum);
  // Re-sync if the route param itself changes (e.g. a direct navigation to a
  // different /admin/analyzer/:id while this wrapper stays mounted).
  useEffect(() => {
    setSelectedCapture(captureIdNum);
  }, [captureIdNum]);
  const updateCaptureUrl = useCaptureSelectHandler();
  const handleCaptureSelect = useCallback(
    (id: number | null) => {
      setSelectedCapture(id ?? undefined);
      updateCaptureUrl(id);
    },
    [updateCaptureUrl]
  );
  const captureLeafLabel = selectedCapture ? `Capture #${selectedCapture}` : null;

  return (
    <AdminToolLayout
      activeTab="analyzer"
      title="Decision Analyzer"
      subtitle="Analyze and replay AI decision prompts"
      leafLabel={captureLeafLabel}
    >
      <DecisionAnalyzer
        embedded
        initialCaptureId={captureIdNum}
        onCaptureSelect={handleCaptureSelect}
      />
    </AdminToolLayout>
  );
}

const VALID_SETTINGS_CATEGORIES: SettingsCategory[] = [
  'models',
  'capture',
  'storage',
  'pricing',
  'appearance',
  'alerting',
  'gameplay',
];

function SettingsWrapper() {
  const { category } = useParams<{ category: string }>();
  const navigate = useNavigate();

  const validCategory = VALID_SETTINGS_CATEGORIES.includes(category as SettingsCategory)
    ? (category as SettingsCategory)
    : 'models';

  // Mobile-only: desktop's back-arrow lives in AdminHeader (useAdminNav).
  const handleBack = () => {
    navigate('/admin', { replace: true });
  };

  const handleCategoryChange = useCallback(
    (newCategory: SettingsCategory) => {
      navigate(`/admin/settings/${newCategory}`, { replace: true });
    },
    [navigate]
  );

  return (
    <AdminToolLayout
      activeTab="settings"
      title="Settings"
      subtitle="Models, capture, storage, and pricing"
      mobileChrome="menu"
      mobileTitle="Settings"
      onMobileBack={handleBack}
    >
      <UnifiedSettings
        embedded
        initialCategory={validCategory}
        onCategoryChange={handleCategoryChange}
      />
    </AdminToolLayout>
  );
}

function AdminTabWrapper() {
  const { tab } = useParams<{ tab: string }>();
  const navigate = useNavigate();

  // Validate the tab parameter
  const validTab = VALID_TABS.includes(tab as AdminTab) ? (tab as AdminTab) : 'personalities';

  // Mobile-only: desktop's back-arrow lives in AdminHeader (useAdminNav). On
  // mobile, go up to the admin menu (replace so the original origin stays as
  // the next back destination).
  const handleBack = () => {
    navigate('/admin', { replace: true });
  };

  const handleTabChange = (newTab: AdminTab) => {
    if (newTab) {
      // Lateral tool switch — replace so "back" always means up the nav path,
      // never "the previous tool I happened to click".
      navigate(`/admin/${newTab}`, { replace: true });
    } else {
      // Going back to admin menu - replace history to preserve original back destination
      navigate('/admin', { replace: true });
    }
  };

  const handleCaptureSelect = useCaptureSelectHandler();

  return (
    <AdminDashboard
      onBack={handleBack}
      initialTab={validTab}
      onTabChange={handleTabChange}
      onCaptureSelect={handleCaptureSelect}
    />
  );
}

function AdminIndex() {
  const navigate = useNavigate();

  // Both desktop and mobile land on the admin tool menu (AdminDashboard with no
  // tab selected) — desktop shows the AdminOverview grid, mobile its own menu.

  const handleBack = () => {
    // At the admin root, "back" exits to wherever the user entered from
    // (the game, the cash lobby, a menu) — captured in sessionStorage on entry.
    navigate(getAdminOrigin());
  };

  const handleTabChange = (newTab: AdminTab) => {
    // Replace /admin with /admin/:tab so the menu doesn't stay in history.
    // When the tab navigates back, it replaces with /admin, keeping one entry.
    navigate(`/admin/${newTab}`, { replace: true });
  };

  return (
    <AdminDashboard onBack={handleBack} initialTab={undefined} onTabChange={handleTabChange} />
  );
}

export function AdminRoutes() {
  const { user, isLoading } = useAuth();
  const canAccessAdmin = hasPermission(user, 'can_access_admin_tools');

  // Show loading while checking auth
  if (isLoading) {
    return (
      <div
        className="admin-loading"
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <div className="admin-loading__spinner" />
      </div>
    );
  }

  // Redirect non-admins to menu
  if (!canAccessAdmin) {
    return <Navigate to="/menu" replace />;
  }

  return (
    <Routes>
      <Route index element={<AdminIndex />} />
      {/* Experiment routes - specific paths must come before :experimentId to match first */}
      <Route path="experiments/new" element={<NewExperimentWrapper />} />
      <Route path="experiments/:experimentId" element={<ExperimentDetailWrapper />} />
      <Route path="replays/:experimentId" element={<ReplayResultsWrapper />} />
      {/* Decision Analyzer with optional capture ID */}
      <Route path="analyzer/:captureId" element={<DecisionAnalyzerWrapper />} />
      {/* Settings sub-category routes */}
      <Route path="settings/:category" element={<SettingsWrapper />} />
      <Route path=":tab" element={<AdminTabWrapper />} />
    </Routes>
  );
}
