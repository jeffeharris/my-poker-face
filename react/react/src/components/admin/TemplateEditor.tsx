import { useState, useEffect, useCallback, useMemo } from 'react';
import { adminAPI } from '../../utils/api';
import { useViewport } from '../../hooks/useViewport';
import './AdminShared.css';
import './TemplateEditor.css';

// ============================================
// Types
// ============================================

interface TemplateSummary {
  name: string;
  version: string;
  section_count: number;
  hash: string;
  variables: string[];
}

interface TemplateDetail {
  name: string;
  version: string;
  hash: string;
  sections: Record<string, string>;
  variables: string[];
}

interface AlertState {
  type: 'success' | 'error' | 'info';
  message: string;
}

interface TemplateEditorProps {
  embedded?: boolean;
}

// ============================================
// Icons
// ============================================

const SearchIcon = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
    <circle cx="8" cy="8" r="5.5" stroke="currentColor" strokeWidth="1.5"/>
    <path d="M12 12L16 16" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
  </svg>
);

const CheckIcon = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
    <path d="M4 9L7.5 12.5L14 5.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
);

const MenuIcon = () => (
  <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
    <path d="M3 5H17M3 10H17M3 15H17" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
  </svg>
);

// ============================================
// Sub-components
// ============================================

interface MasterListProps {
  templates: TemplateSummary[];
  selected: string | null;
  onSelect: (name: string) => void;
  search: string;
  onSearchChange: (search: string) => void;
}

function MasterList({ templates, selected, onSelect, search, onSearchChange }: MasterListProps) {
  const filtered = useMemo(() =>
    templates.filter(t =>
      t.name.toLowerCase().includes(search.toLowerCase())
    ),
    [templates, search]
  );

  return (
    <>
      <div className="admin-master__header">
        <h3 className="admin-master__title">Templates</h3>
        <span className="admin-master__count">{templates.length}</span>
      </div>
      <div className="admin-master__search">
        <div className="admin-master__search-wrap">
          <span className="admin-master__search-icon">
            <SearchIcon />
          </span>
          <input
            type="text"
            className="admin-master__search-input"
            placeholder="Search..."
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
          />
        </div>
      </div>
      <div className="admin-master__list">
        {filtered.map((t) => (
          <button
            key={t.name}
            type="button"
            className={`admin-master__item ${selected === t.name ? 'admin-master__item--selected' : ''}`}
            onClick={() => onSelect(t.name)}
          >
            <span className="admin-master__item-avatar">{t.name.charAt(0).toUpperCase()}</span>
            <div className="admin-master__item-content">
              <span className="admin-master__item-name">{t.name}</span>
              <span className="te-master__item-meta">v{t.version} • {t.section_count} sections</span>
            </div>
            {selected === t.name && (
              <span className="admin-master__item-check">
                <CheckIcon />
              </span>
            )}
          </button>
        ))}
        {filtered.length === 0 && (
          <div className="admin-master__empty">
            No templates found{search ? ` matching "${search}"` : ''}
          </div>
        )}
      </div>
    </>
  );
}

// ============================================
// Main Component
// ============================================

export function TemplateEditor({ embedded = false }: TemplateEditorProps) {
  // Responsive breakpoints
  const { isDesktop, isTablet } = useViewport();

  // Core state
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<TemplateDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [alert, setAlert] = useState<AlertState | null>(null);
  const [editedSections, setEditedSections] = useState<Record<string, string>>({});
  const [previewVariables, setPreviewVariables] = useState<Record<string, string>>({});
  const [previewResult, setPreviewResult] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState<string>('');
  const [isDirty, setIsDirty] = useState(false);

  // UI state for master-detail
  const [masterSearch, setMasterSearch] = useState('');
  const [masterPanelOpen, setMasterPanelOpen] = useState(false);

  // Fetch template list
  const fetchTemplates = useCallback(async () => {
    try {
      setLoading(true);
      const response = await adminAPI.fetch('/admin/api/prompts/templates');
      const data = await response.json();

      if (data.success) {
        setTemplates(data.templates);
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to load templates' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTemplates();
  }, [fetchTemplates]);

  // Fetch template details
  const fetchTemplate = async (name: string) => {
    if (isDirty && !window.confirm('You have unsaved changes. Discard them?')) {
      return;
    }

    try {
      const response = await adminAPI.fetch(`/admin/api/prompts/templates/${name}`);
      const data = await response.json();

      if (data.success) {
        setSelectedTemplate(data.template);
        setEditedSections(data.template.sections);
        setActiveSection(Object.keys(data.template.sections)[0] || '');
        setPreviewVariables({});
        setPreviewResult(null);
        setIsDirty(false);
        // Close master panel on tablet after selection
        if (!isDesktop) {
          setMasterPanelOpen(false);
        }
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to load template' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    }
  };

  // Update section content
  const updateSection = (section: string, content: string) => {
    setEditedSections(prev => ({ ...prev, [section]: content }));
    setIsDirty(true);
  };

  // Save template
  const saveTemplate = async () => {
    if (!selectedTemplate) return;

    try {
      setSaving(true);
      const response = await adminAPI.fetch(`/admin/api/prompts/templates/${selectedTemplate.name}`, {
        method: 'PUT',
        body: JSON.stringify({
          sections: editedSections,
        }),
      });

      const data = await response.json();

      if (data.success) {
        setAlert({ type: 'success', message: 'Template saved successfully' });
        setIsDirty(false);
        fetchTemplates();
        // Update selected template with new hash
        setSelectedTemplate(prev => prev ? { ...prev, hash: data.new_hash } : null);
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to save template' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    } finally {
      setSaving(false);
    }
  };

  // Preview template
  const previewTemplate = async () => {
    if (!selectedTemplate) return;

    try {
      const response = await adminAPI.fetch(`/admin/api/prompts/templates/${selectedTemplate.name}/preview`, {
        method: 'POST',
        body: JSON.stringify({
          sections: editedSections,
          variables: previewVariables,
        }),
      });

      const data = await response.json();

      if (data.success) {
        setPreviewResult(data.rendered);
        if (data.missing_variables?.length > 0) {
          setAlert({ type: 'info', message: `Missing variables: ${data.missing_variables.join(', ')}` });
        }
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to preview template' });
      }
    } catch {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    }
  };

  // Clear alert after timeout
  useEffect(() => {
    if (alert) {
      const timer = setTimeout(() => setAlert(null), 3000);
      return () => clearTimeout(timer);
    }
  }, [alert]);

  // Loading state
  if (loading) {
    return (
      <div className="admin-loading">
        <div className="admin-loading__spinner" />
        <span className="admin-loading__text">Loading templates...</span>
      </div>
    );
  }

  // Editor content (scrollable)
  const editorContent = selectedTemplate ? (
    <>
      {/* Section Tabs */}
      <div className="te-tabs">
        {Object.keys(editedSections).map(section => (
          <button
            key={section}
            className={`te-tab ${activeSection === section ? 'te-tab--active' : ''}`}
            onClick={() => setActiveSection(section)}
            type="button"
          >
            {section}
          </button>
        ))}
      </div>

      {/* Editor Content */}
      <div className="te-editor-content">
        <textarea
          className="te-textarea"
          value={editedSections[activeSection] || ''}
          onChange={(e) => updateSection(activeSection, e.target.value)}
          spellCheck={false}
        />
      </div>

      {/* Variables */}
      {selectedTemplate.variables.length > 0 && (
        <div className="te-variables">
          <div className="te-variables__header">Preview Variables</div>
          <div className="te-variables__grid">
            {selectedTemplate.variables.map(v => (
              <div key={v} className="te-variables__item">
                <label>{v}</label>
                <input
                  type="text"
                  className="admin-input"
                  value={previewVariables[v] || ''}
                  onChange={(e) => setPreviewVariables(prev => ({ ...prev, [v]: e.target.value }))}
                  placeholder={`{${v}}`}
                />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Preview Result */}
      {previewResult && (
        <div className="te-preview">
          <div className="te-preview__header">
            Preview Output
            <button
              className="te-preview__close"
              onClick={() => setPreviewResult(null)}
            >
              ×
            </button>
          </div>
          <pre className="te-preview__content">{previewResult}</pre>
        </div>
      )}
    </>
  ) : null;

  // Empty state content
  const emptyContent = (
    <div className="admin-detail__empty">
      <div className="admin-detail__empty-icon">📝</div>
      <h3 className="admin-detail__empty-title">No Template Selected</h3>
      <p className="admin-detail__empty-description">
        Select a template from the list to edit its sections
      </p>
    </div>
  );

  return (
    <div className={`te-container ${embedded ? 'te-container--embedded' : ''}`}>
      {/* Alert Toast */}
      {alert && (
        <div className="admin-toast-container">
          <div className={`admin-alert admin-alert--${alert.type}`}>
            <span className="admin-alert__icon">
              {alert.type === 'success' && '✓'}
              {alert.type === 'error' && '✕'}
              {alert.type === 'info' && 'ℹ'}
            </span>
            <span className="admin-alert__content">{alert.message}</span>
            <button className="admin-alert__dismiss" onClick={() => setAlert(null)}>×</button>
          </div>
        </div>
      )}

      {/* Header - only show when not embedded */}
      {!embedded && (
        <div className="admin-header">
          <h2 className="admin-header__title">Prompt Templates</h2>
          <p className="admin-header__subtitle">Edit system prompt templates for AI players</p>
        </div>
      )}

      {/* Master-Detail Layout */}
      <div className="admin-master-detail">
        {/* Master Panel (sidebar) */}
        <aside className={`admin-master ${masterPanelOpen || isDesktop ? 'admin-master--open' : ''}`}>
          <MasterList
            templates={templates}
            selected={selectedTemplate?.name || null}
            onSelect={fetchTemplate}
            search={masterSearch}
            onSearchChange={setMasterSearch}
          />
        </aside>

        {/* Detail Panel */}
        <main className="admin-detail">
          {/* Tablet toggle button (hidden on desktop) */}
          {isTablet && !isDesktop && (
            <button
              type="button"
              className="admin-master-toggle"
              onClick={() => setMasterPanelOpen(!masterPanelOpen)}
            >
              <MenuIcon />
              <span>{selectedTemplate?.name || 'Select Template'}</span>
            </button>
          )}

          {/* Detail header when template selected */}
          {selectedTemplate && (
            <div className="admin-detail__header">
              <div>
                <h2 className="admin-detail__title">{selectedTemplate.name}</h2>
                <p className="admin-detail__subtitle">
                  v{selectedTemplate.version} • {Object.keys(selectedTemplate.sections).length} sections
                </p>
              </div>
            </div>
          )}

          {/* Detail content (scrollable) */}
          <div className="admin-detail__content">
            {editorContent || emptyContent}
          </div>

          {/* Action bar (fixed at bottom) */}
          {selectedTemplate && (
            <div className="admin-detail__footer">
              <div className="admin-detail__footer-secondary">
                <button
                  className="admin-btn admin-btn--secondary"
                  onClick={previewTemplate}
                >
                  Preview
                </button>
              </div>
              <div className="admin-detail__footer-primary">
                <button
                  className="admin-btn admin-btn--primary"
                  onClick={saveTemplate}
                  disabled={!isDirty || saving}
                >
                  {saving ? 'Saving...' : isDirty ? 'Save Changes' : 'Saved'}
                </button>
              </div>
            </div>
          )}
        </main>

        {/* Backdrop for tablet sidebar */}
        {isTablet && !isDesktop && masterPanelOpen && (
          <div
            className="te-backdrop"
            onClick={() => setMasterPanelOpen(false)}
          />
        )}
      </div>
    </div>
  );
}

export default TemplateEditor;
