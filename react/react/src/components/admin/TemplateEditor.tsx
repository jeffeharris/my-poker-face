import { useState, useEffect, useCallback } from 'react';
import { config } from '../../config';
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
// Main Component
// ============================================

export function TemplateEditor({ embedded = false }: TemplateEditorProps) {
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

  // Fetch template list
  const fetchTemplates = useCallback(async () => {
    try {
      setLoading(true);
      const response = await fetch(`${config.API_URL}/admin/api/prompts/templates`);
      const data = await response.json();

      if (data.success) {
        setTemplates(data.templates);
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to load templates' });
      }
    } catch (error) {
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
      const response = await fetch(`${config.API_URL}/admin/api/prompts/templates/${name}`);
      const data = await response.json();

      if (data.success) {
        setSelectedTemplate(data.template);
        setEditedSections(data.template.sections);
        setActiveSection(Object.keys(data.template.sections)[0] || '');
        setPreviewVariables({});
        setPreviewResult(null);
        setIsDirty(false);
      } else {
        setAlert({ type: 'error', message: data.error || 'Failed to load template' });
      }
    } catch (error) {
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
      const response = await fetch(`${config.API_URL}/admin/api/prompts/templates/${selectedTemplate.name}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
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
    } catch (error) {
      setAlert({ type: 'error', message: 'Failed to connect to server' });
    } finally {
      setSaving(false);
    }
  };

  // Preview template
  const previewTemplate = async () => {
    if (!selectedTemplate) return;

    try {
      const response = await fetch(`${config.API_URL}/admin/api/prompts/templates/${selectedTemplate.name}/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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
    } catch (error) {
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

  if (loading) {
    return (
      <div className="te-loading">
        <div className="te-loading__spinner" />
        <span>Loading templates...</span>
      </div>
    );
  }

  return (
    <div className={`te-container ${embedded ? 'te-container--embedded' : ''}`}>
      {/* Alert Toast */}
      {alert && (
        <div className={`te-alert te-alert--${alert.type}`}>
          <span className="te-alert__icon">
            {alert.type === 'success' ? '✓' : alert.type === 'error' ? '✕' : 'i'}
          </span>
          <span className="te-alert__message">{alert.message}</span>
          <button className="te-alert__close" onClick={() => setAlert(null)}>×</button>
        </div>
      )}

      {/* Header */}
      <div className="te-header">
        <h2 className="te-header__title">Prompt Templates</h2>
        <p className="te-header__subtitle">Edit system prompt templates for AI players</p>
      </div>

      <div className="te-layout">
        {/* Template List */}
        <div className="te-sidebar">
          <div className="te-sidebar__header">Templates</div>
          <div className="te-sidebar__list">
            {templates.map(t => (
              <button
                key={t.name}
                className={`te-sidebar__item ${selectedTemplate?.name === t.name ? 'te-sidebar__item--active' : ''}`}
                onClick={() => fetchTemplate(t.name)}
                type="button"
              >
                <span className="te-sidebar__item-name">{t.name}</span>
                <span className="te-sidebar__item-meta">v{t.version} • {t.section_count} sections</span>
              </button>
            ))}
          </div>
        </div>

        {/* Editor */}
        <div className="te-editor">
          {selectedTemplate ? (
            <>
              {/* Section Tabs */}
              <div className="te-editor__tabs">
                {Object.keys(editedSections).map(section => (
                  <button
                    key={section}
                    className={`te-editor__tab ${activeSection === section ? 'te-editor__tab--active' : ''}`}
                    onClick={() => setActiveSection(section)}
                    type="button"
                  >
                    {section}
                  </button>
                ))}
              </div>

              {/* Editor Content */}
              <div className="te-editor__content">
                <textarea
                  className="te-editor__textarea"
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
                          className="te-input"
                          value={previewVariables[v] || ''}
                          onChange={(e) => setPreviewVariables(prev => ({ ...prev, [v]: e.target.value }))}
                          placeholder={`{${v}}`}
                        />
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Actions */}
              <div className="te-editor__actions">
                <button
                  className="te-btn te-btn--ghost"
                  onClick={previewTemplate}
                >
                  Preview
                </button>
                <button
                  className="te-btn te-btn--primary"
                  onClick={saveTemplate}
                  disabled={!isDirty || saving}
                >
                  {saving ? 'Saving...' : isDirty ? 'Save Changes' : 'Saved'}
                </button>
              </div>

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
          ) : (
            <div className="te-editor__empty">
              Select a template to edit
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default TemplateEditor;
