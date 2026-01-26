import { useState, useEffect, useCallback } from 'react';
import { Routes, Route, Navigate, useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, MessageSquare, X, Send, Loader2, Columns2, Maximize2 } from 'lucide-react';
import { AdminDashboard } from './AdminDashboard';
import { SIDEBAR_ITEMS } from './adminSidebarItems';
import { AdminSidebar } from './AdminSidebar';
import { ExperimentDesigner, ExperimentChat, type AssistantPanelProps } from './ExperimentDesigner';
import { ExperimentDetail } from './ExperimentDesigner/ExperimentDetail';
import { ReplayResults } from './ReplayResults';
import { DecisionAnalyzer } from './DecisionAnalyzer';
import { useViewport } from '../../hooks/useViewport';
import { useAuth, hasPermission } from '../../hooks/useAuth';
import { config } from '../../config';
import type { AdminTab } from './AdminSidebar';

const VALID_TABS: AdminTab[] = ['users', 'personalities', 'analyzer', 'playground', 'experiments', 'presets', 'templates', 'settings', 'debug'];

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
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  // Page-level assistant state
  const [showAssistant, setShowAssistant] = useState(false);
  const [assistantMode, setAssistantMode] = useState<'overlay' | 'docked'>(() => {
    const saved = localStorage.getItem('experimentAssistantMode');
    return (saved === 'overlay' || saved === 'docked') ? saved : 'docked';
  });
  const [chatMessages, setChatMessages] = useState<Array<{ role: 'user' | 'assistant'; content: string }>>([]);
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);

  const experimentIdNum = experimentId ? parseInt(experimentId, 10) : NaN;

  // Load chat history when opening assistant
  const handleOpenAssistant = async () => {
    setShowAssistant(true);
    if (chatMessages.length === 0 && !isNaN(experimentIdNum)) {
      try {
        const response = await fetch(`${config.API_URL}/api/experiments/${experimentIdNum}/chat/history`);
        const data = await response.json();
        if (data.success && data.history) {
          setChatMessages(data.history);
        }
      } catch (err) {
        console.error('Failed to load chat history:', err);
      }
    }
  };

  const handleSendMessage = async () => {
    if (!chatInput.trim() || chatLoading || isNaN(experimentIdNum)) return;

    const message = chatInput.trim();
    setChatInput('');
    setChatMessages(prev => [...prev, { role: 'user', content: message }]);
    setChatLoading(true);

    try {
      const response = await fetch(`${config.API_URL}/api/experiments/${experimentIdNum}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message }),
      });
      const data = await response.json();
      if (data.success) {
        setChatMessages(prev => [...prev, { role: 'assistant', content: data.response }]);
      } else {
        setChatMessages(prev => [...prev, { role: 'assistant', content: `Error: ${data.error}` }]);
      }
    } catch {
      setChatMessages(prev => [...prev, { role: 'assistant', content: 'Error: Failed to connect to server' }]);
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
      console.error('Failed to clear chat history:', err);
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

  const handleEditInLabAssistant = (experiment: Parameters<NonNullable<React.ComponentProps<typeof ExperimentDetail>['onEditInLabAssistant']>>[0]) => {
    navigate('/admin/experiments', { state: { editExperiment: experiment } });
  };

  const handleBuildFromSuggestion = (
    experiment: Parameters<NonNullable<React.ComponentProps<typeof ExperimentDetail>['onBuildFromSuggestion']>>[0],
    suggestion: Parameters<NonNullable<React.ComponentProps<typeof ExperimentDetail>['onBuildFromSuggestion']>>[1]
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
        onSubmit={(e) => { e.preventDefault(); handleSendMessage(); }}
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

  // Mobile layout
  if (isMobile) {
    return (
      <div className="admin-dashboard-layout admin-dashboard-layout--mobile">
        <div className="admin-main__content admin-main__content--mobile">
          <ExperimentDetail
            experimentId={experimentIdNum}
            onBack={handleBack}
            onEditInLabAssistant={handleEditInLabAssistant}
            onBuildFromSuggestion={handleBuildFromSuggestion}
            onOpenAssistant={handleOpenAssistant}
          />
        </div>
        {/* Mobile always uses overlay */}
        {showAssistant && (
          <div className="admin-assistant-overlay" onClick={() => setShowAssistant(false)}>
            <div onClick={(e) => e.stopPropagation()}>
              <AssistantPanel isDocked={false} />
            </div>
          </div>
        )}
      </div>
    );
  }

  // Desktop layout with sidebar + content + optional docked assistant
  return (
    <div className={`admin-dashboard-layout ${showDockedAssistant ? 'admin-dashboard-layout--with-assistant' : ''}`}>
      <AdminSidebar
        items={SIDEBAR_ITEMS}
        activeTab="experiments"
        onTabChange={(tab) => navigate(`/admin/${tab}`)}
        collapsed={sidebarCollapsed}
        onCollapsedChange={setSidebarCollapsed}
      />
      <main className="admin-main">
        <header className="admin-main__header">
          <button
            className="admin-main__back"
            onClick={handleBack}
            aria-label="Go back to experiments"
          >
            <ArrowLeft size={20} />
          </button>
          <div className="admin-main__header-text">
            <h1 className="admin-main__title">Experiment Details</h1>
            <p className="admin-main__subtitle">View experiment results and analysis</p>
          </div>
        </header>
        <div className="admin-main__content">
          <ExperimentDetail
            experimentId={experimentIdNum}
            onBack={handleBack}
            onEditInLabAssistant={handleEditInLabAssistant}
            onBuildFromSuggestion={handleBuildFromSuggestion}
            onOpenAssistant={handleOpenAssistant}
          />
        </div>
      </main>

      {/* Docked Assistant Panel (page-level) */}
      {showDockedAssistant && <AssistantPanel isDocked={true} />}

      {/* Overlay Assistant Panel */}
      {showAssistant && !isDockedMode && (
        <div className="admin-assistant-overlay" onClick={() => setShowAssistant(false)}>
          <div onClick={(e) => e.stopPropagation()}>
            <AssistantPanel isDocked={false} />
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Wrapper for replay experiment results view with URL params
 */
function ReplayResultsWrapper() {
  const { experimentId } = useParams<{ experimentId: string }>();
  const navigate = useNavigate();
  const { isMobile } = useViewport();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const experimentIdNum = experimentId ? parseInt(experimentId, 10) : NaN;

  const handleBack = () => {
    navigate('/admin/experiments');
  };

  if (!experimentId || isNaN(experimentIdNum)) {
    return <Navigate to="/admin/experiments" replace />;
  }

  // Mobile layout
  if (isMobile) {
    return (
      <div className="admin-dashboard-layout admin-dashboard-layout--mobile">
        <div className="admin-main__content admin-main__content--mobile">
          <ReplayResults
            experimentId={experimentIdNum}
            onBack={handleBack}
          />
        </div>
      </div>
    );
  }

  // Desktop layout with sidebar + content
  return (
    <div className="admin-dashboard-layout">
      <AdminSidebar
        items={SIDEBAR_ITEMS}
        activeTab="experiments"
        onTabChange={(tab) => navigate(`/admin/${tab}`)}
        collapsed={sidebarCollapsed}
        onCollapsedChange={setSidebarCollapsed}
      />
      <main className="admin-main">
        <header className="admin-main__header">
          <button
            className="admin-main__back"
            onClick={handleBack}
            aria-label="Go back to experiments"
          >
            <ArrowLeft size={20} />
          </button>
          <div className="admin-main__header-text">
            <h1 className="admin-main__title">Replay Results</h1>
            <p className="admin-main__subtitle">View replay experiment results and analysis</p>
          </div>
        </header>
        <div className="admin-main__content">
          <ReplayResults
            experimentId={experimentIdNum}
            onBack={handleBack}
          />
        </div>
      </main>
    </div>
  );
}

/**
 * Wrapper for new experiment design page (/admin/experiments/new)
 */
function NewExperimentWrapper() {
  const navigate = useNavigate();
  const { isMobile } = useViewport();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [assistantPanelProps, setAssistantPanelProps] = useState<AssistantPanelProps | null>(null);

  const handleBack = () => {
    navigate('/admin/experiments');
  };

  // Mobile layout
  if (isMobile) {
    return (
      <div className="admin-dashboard-layout admin-dashboard-layout--mobile">
        <div className="admin-main__content admin-main__content--mobile">
          <ExperimentDesigner
            embedded
            initialMode="design"
            onAssistantPanelChange={setAssistantPanelProps}
          />
        </div>
      </div>
    );
  }

  // Desktop layout with sidebar + content + assistant panel
  return (
    <div className={`admin-dashboard-layout ${assistantPanelProps ? 'admin-dashboard-layout--with-assistant' : ''}`}>
      <AdminSidebar
        items={SIDEBAR_ITEMS}
        activeTab="experiments"
        onTabChange={(tab) => navigate(`/admin/${tab}`)}
        collapsed={sidebarCollapsed}
        onCollapsedChange={setSidebarCollapsed}
      />
      <main className="admin-main">
        <header className="admin-main__header">
          <button
            className="admin-main__back"
            onClick={handleBack}
            aria-label="Go back to experiments"
          >
            <ArrowLeft size={20} />
          </button>
          <div className="admin-main__header-text">
            <h1 className="admin-main__title">New Experiment</h1>
            <p className="admin-main__subtitle">Design a new experiment with the Lab Assistant</p>
          </div>
        </header>
        <div className="admin-main__content">
          <ExperimentDesigner
            embedded
            initialMode="design"
            onAssistantPanelChange={setAssistantPanelProps}
          />
        </div>
      </main>

      {/* Docked Assistant Panel (page-level) */}
      {assistantPanelProps && (
        <div className="admin-assistant-panel admin-assistant-panel--docked">
          <div className="admin-assistant-panel__header">
            <h3>
              <MessageSquare size={18} />
              Lab Assistant
            </h3>
          </div>
          <ExperimentChat {...assistantPanelProps} />
        </div>
      )}
    </div>
  );
}

/**
 * Wrapper for Decision Analyzer with URL-based capture selection
 */
function DecisionAnalyzerWrapper() {
  const { captureId } = useParams<{ captureId: string }>();
  const navigate = useNavigate();
  const { isMobile } = useViewport();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const captureIdNum = captureId ? parseInt(captureId, 10) : undefined;

  const handleBack = () => {
    navigate('/admin/analyzer');
  };

  const handleCaptureSelect = useCaptureSelectHandler();

  // Mobile layout
  if (isMobile) {
    return (
      <div className="admin-dashboard-layout admin-dashboard-layout--mobile">
        <div className="admin-main__content admin-main__content--mobile">
          <DecisionAnalyzer
            embedded
            initialCaptureId={captureIdNum}
            onCaptureSelect={handleCaptureSelect}
          />
        </div>
      </div>
    );
  }

  // Desktop layout with sidebar + content
  return (
    <div className="admin-dashboard-layout">
      <AdminSidebar
        items={SIDEBAR_ITEMS}
        activeTab="analyzer"
        onTabChange={(tab) => navigate(`/admin/${tab}`)}
        collapsed={sidebarCollapsed}
        onCollapsedChange={setSidebarCollapsed}
      />
      <main className="admin-main">
        <header className="admin-main__header">
          {captureIdNum && (
            <button
              className="admin-main__back"
              onClick={handleBack}
              aria-label="Go back to analyzer list"
            >
              <ArrowLeft size={20} />
            </button>
          )}
          <div className="admin-main__header-text">
            <h1 className="admin-main__title">Decision Analyzer</h1>
            <p className="admin-main__subtitle">
              {captureIdNum ? `Capture #${captureIdNum}` : 'Analyze and replay AI decision prompts'}
            </p>
          </div>
        </header>
        <div className="admin-main__content">
          <DecisionAnalyzer
            embedded
            initialCaptureId={captureIdNum}
            onCaptureSelect={handleCaptureSelect}
          />
        </div>
      </main>
    </div>
  );
}

function AdminTabWrapper() {
  const { tab } = useParams<{ tab: string }>();
  const navigate = useNavigate();
  const { isMobile } = useViewport();

  // Validate the tab parameter
  const validTab = VALID_TABS.includes(tab as AdminTab) ? (tab as AdminTab) : 'personalities';

  const handleBack = () => {
    // On mobile, go back to admin menu; on desktop, go to main menu
    if (isMobile) {
      navigate('/admin');
    } else {
      navigate('/menu');
    }
  };

  const handleTabChange = (newTab: AdminTab) => {
    navigate(`/admin/${newTab}`);
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
  const { isMobile } = useViewport();

  // On desktop, redirect to personalities tab
  // On mobile, show the menu via AdminDashboard with no tab selected
  if (!isMobile) {
    return <Navigate to="/admin/personalities" replace />;
  }

  const handleBack = () => {
    navigate('/menu');
  };

  const handleTabChange = (newTab: AdminTab) => {
    navigate(`/admin/${newTab}`);
  };

  return (
    <AdminDashboard
      onBack={handleBack}
      initialTab={undefined}
      onTabChange={handleTabChange}
    />
  );
}

export function AdminRoutes() {
  const { user, isLoading } = useAuth();
  const canAccessAdmin = hasPermission(user, 'can_access_admin_tools');

  // Show loading while checking auth
  if (isLoading) {
    return (
      <div className="admin-loading" style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
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
      <Route path=":tab" element={<AdminTabWrapper />} />
    </Routes>
  );
}
