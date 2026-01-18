/**
 * Template Editor component for viewing and editing prompt templates.
 */
import { useState, useEffect, useCallback } from 'react';
import { adminAPI } from '../../../utils/api';
import type { TemplateSummary, PromptTemplate, PlaygroundCapture, ReplayResponse } from './types';

interface Props {
  onNavigateToCapture?: (captureId: number) => void;
}

// Map template names to call_type values for filtering captures
const TEMPLATE_TO_CALL_TYPE: Record<string, string[]> = {
  'poker_player': ['player_decision'],
  'decision': ['player_decision'],
  'end_of_hand_commentary': ['commentary'],
  'quick_chat_tilt': ['targeted_chat'],
  'quick_chat_false_confidence': ['targeted_chat'],
  'quick_chat_doubt': ['targeted_chat'],
  'quick_chat_goad': ['targeted_chat'],
  'quick_chat_mislead': ['targeted_chat'],
  'quick_chat_befriend': ['targeted_chat'],
  'quick_chat_table': ['targeted_chat'],
  'post_round_gloat': ['post_round_chat'],
  'post_round_humble': ['post_round_chat'],
  'post_round_salty': ['post_round_chat'],
  'post_round_gracious': ['post_round_chat'],
};

export function TemplateEditor({ onNavigateToCapture }: Props) {
  // State
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<PromptTemplate | null>(null);
  const [editedSections, setEditedSections] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [isDirty, setIsDirty] = useState(false);

  // Captures for testing
  const [relatedCaptures, setRelatedCaptures] = useState<PlaygroundCapture[]>([]);
  const [loadingCaptures, setLoadingCaptures] = useState(false);
  const [testingCaptureId, setTestingCaptureId] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<ReplayResponse | null>(null);
  const [showTestResults, setShowTestResults] = useState(false);

  // Fetch template list
  const fetchTemplates = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const response = await adminAPI.fetch('/admin/api/prompts/templates');
      const data = await response.json();

      if (data.success) {
        setTemplates(data.templates);
      } else {
        setError(data.error || 'Failed to fetch templates');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch templates');
    } finally {
      setLoading(false);
    }
  }, []);

  // Fetch single template
  const fetchTemplate = async (name: string) => {
    try {
      const response = await adminAPI.fetch(`/admin/api/prompts/templates/${name}`);
      const data = await response.json();

      if (data.success) {
        setSelectedTemplate(data.template);
        setEditedSections({ ...data.template.sections });
        setIsDirty(false);
        setSuccessMessage(null);
        setTestResult(null);
        setShowTestResults(false);

        // Fetch related captures
        fetchRelatedCaptures(name);
      } else {
        setError(data.error || 'Failed to fetch template');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch template');
    }
  };

  // Fetch captures related to this template
  const fetchRelatedCaptures = async (templateName: string) => {
    const callTypes = TEMPLATE_TO_CALL_TYPE[templateName];
    if (!callTypes || callTypes.length === 0) {
      setRelatedCaptures([]);
      return;
    }

    setLoadingCaptures(true);
    try {
      // Fetch captures for each call type
      const allCaptures: PlaygroundCapture[] = [];
      for (const callType of callTypes) {
        const response = await adminAPI.fetch(
          `/admin/api/playground/captures?call_type=${callType}&limit=10`
        );
        const data = await response.json();
        if (data.success && data.captures) {
          allCaptures.push(...data.captures);
        }
      }
      // Sort by created_at descending and take first 10
      allCaptures.sort((a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );
      setRelatedCaptures(allCaptures.slice(0, 10));
    } catch (err) {
      console.error('Failed to fetch related captures:', err);
      setRelatedCaptures([]);
    } finally {
      setLoadingCaptures(false);
    }
  };

  // Test edited template on a captured prompt
  const handleTestCapture = async (captureId: number) => {
    setTestingCaptureId(captureId);
    setTestResult(null);
    setShowTestResults(true);

    try {
      const response = await adminAPI.fetch(
        `/admin/api/playground/captures/${captureId}/replay`,
        {
          method: 'POST',
          body: JSON.stringify({
            use_history: true,
          }),
        }
      );
      const data = await response.json();
      setTestResult(data);
    } catch (err) {
      setTestResult({
        success: false,
        error: err instanceof Error ? err.message : 'Test failed',
        original_response: '',
        new_response: '',
        provider_used: '',
        model_used: '',
        input_tokens: 0,
        output_tokens: 0,
        latency_ms: null,
      });
    } finally {
      setTestingCaptureId(null);
    }
  };

  // Save template
  const handleSave = async () => {
    if (!selectedTemplate || !isDirty) return;

    setSaving(true);
    setError(null);
    setSuccessMessage(null);

    try {
      const response = await adminAPI.fetch(
        `/admin/api/prompts/templates/${selectedTemplate.name}`,
        {
          method: 'PUT',
          body: JSON.stringify({ sections: editedSections }),
        }
      );
      const data = await response.json();

      if (data.success) {
        setSuccessMessage('Template saved! Hot-reload will pick up changes.');
        setIsDirty(false);
        // Refresh template list and current template
        fetchTemplates();
        fetchTemplate(selectedTemplate.name);
      } else {
        setError(data.error || 'Failed to save template');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save template');
    } finally {
      setSaving(false);
    }
  };

  // Handle section edit
  const handleSectionChange = (sectionName: string, content: string) => {
    setEditedSections(prev => ({ ...prev, [sectionName]: content }));
    setIsDirty(true);
    setSuccessMessage(null);
  };

  // Reset changes
  const handleReset = () => {
    if (selectedTemplate) {
      setEditedSections({ ...selectedTemplate.sections });
      setIsDirty(false);
    }
  };

  // Initial fetch
  useEffect(() => {
    fetchTemplates();
  }, [fetchTemplates]);

  // Extract variables from edited content
  const extractVariables = (content: string): string[] => {
    const matches = content.match(/(?<!\{)\{(\w+)\}(?!\})/g) || [];
    const vars = matches.map(m => m.slice(1, -1));
    return [...new Set(vars)].sort();
  };

  // Get all variables from edited sections
  const allEditedContent = Object.values(editedSections).join('\n');
  const currentVariables = extractVariables(allEditedContent);

  return (
    <div className="template-editor">
      {/* Template list */}
      <div className="template-list-panel">
        <div className="template-list-header">
          <h3>Templates</h3>
          <button onClick={fetchTemplates} disabled={loading} className="refresh-btn">
            {loading ? '...' : '↻'}
          </button>
        </div>

        <div className="template-list">
          {templates.map(template => (
            <div
              key={template.name}
              className={`template-item ${selectedTemplate?.name === template.name ? 'selected' : ''}`}
              onClick={() => {
                if (isDirty && !confirm('You have unsaved changes. Continue?')) return;
                fetchTemplate(template.name);
              }}
            >
              <div className="template-name">{template.name}</div>
              <div className="template-meta">
                <span className="version">v{template.version}</span>
                <span className="sections">{template.section_count} sections</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Editor panel */}
      <div className="template-editor-panel">
        {selectedTemplate ? (
          <>
            {/* Header */}
            <div className="editor-header">
              <div className="editor-title">
                <h3>{selectedTemplate.name}</h3>
                <span className="version">v{selectedTemplate.version}</span>
                <span className="hash">{selectedTemplate.hash}</span>
                {isDirty && <span className="dirty-badge">Unsaved</span>}
              </div>
              <div className="editor-actions">
                <button onClick={handleReset} disabled={!isDirty || saving}>
                  Reset
                </button>
                <button
                  onClick={handleSave}
                  disabled={!isDirty || saving}
                  className="save-btn"
                >
                  {saving ? 'Saving...' : 'Save'}
                </button>
              </div>
            </div>

            {/* Messages */}
            {error && <div className="error-message">{error}</div>}
            {successMessage && <div className="success-message">{successMessage}</div>}

            {/* Variables info */}
            <div className="variables-info">
              <span className="label">Variables:</span>
              <div className="variable-list">
                {currentVariables.map(v => (
                  <code key={v} className="variable">{`{${v}}`}</code>
                ))}
              </div>
            </div>

            {/* Section editors */}
            <div className="sections-container">
              {Object.entries(editedSections).map(([sectionName, content]) => (
                <div key={sectionName} className="section-editor">
                  <div className="section-header">
                    <h4>{sectionName}</h4>
                    <span className="char-count">{content.length} chars</span>
                  </div>
                  <textarea
                    value={content}
                    onChange={(e) => handleSectionChange(sectionName, e.target.value)}
                    rows={Math.max(5, content.split('\n').length + 2)}
                    spellCheck={false}
                  />
                </div>
              ))}
            </div>

            {/* Related captures for testing */}
            <div className="captures-section">
              <div className="captures-header">
                <h3>Test on Past Calls</h3>
                {loadingCaptures && <span className="loading-indicator">Loading...</span>}
              </div>

              {relatedCaptures.length > 0 ? (
                <div className="captures-list">
                  {relatedCaptures.map(capture => (
                    <div key={capture.id} className="capture-test-item">
                      <div className="capture-info">
                        <span className="capture-player">{capture.player_name || 'Unknown'}</span>
                        <span className="capture-phase">@ {capture.phase}</span>
                        {capture.action_taken && (
                          <span className="capture-action">- {capture.action_taken}</span>
                        )}
                        <span className="capture-time">
                          {new Date(capture.created_at).toLocaleDateString()}
                        </span>
                      </div>
                      <div className="capture-actions">
                        <button
                          onClick={() => handleTestCapture(capture.id)}
                          disabled={testingCaptureId === capture.id}
                          className="test-btn"
                        >
                          {testingCaptureId === capture.id ? 'Testing...' : 'Test'}
                        </button>
                        {onNavigateToCapture && (
                          <button
                            onClick={() => onNavigateToCapture(capture.id)}
                            className="replay-btn"
                          >
                            Replay
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="no-captures">
                  {loadingCaptures
                    ? 'Loading captures...'
                    : 'No captured calls found for this template type'}
                </div>
              )}

              {/* Test Results */}
              {showTestResults && (
                <div className="test-results">
                  <div className="test-results-header">
                    <h4>Test Results</h4>
                    <button onClick={() => setShowTestResults(false)} className="close-btn">
                      ×
                    </button>
                  </div>
                  {testingCaptureId ? (
                    <div className="test-loading">Running test...</div>
                  ) : testResult ? (
                    testResult.error ? (
                      <div className="test-error">{testResult.error}</div>
                    ) : (
                      <div className="test-comparison">
                        <div className="comparison-panel original">
                          <h5>Original Response</h5>
                          <pre>{testResult.original_response}</pre>
                        </div>
                        <div className="comparison-panel new">
                          <h5>New Response ({testResult.model_used})</h5>
                          <pre>{testResult.new_response}</pre>
                        </div>
                        <div className="test-metrics">
                          <span>Provider: {testResult.provider_used}</span>
                          <span>Latency: {testResult.latency_ms}ms</span>
                          <span>Tokens: {testResult.input_tokens} in / {testResult.output_tokens} out</span>
                        </div>
                      </div>
                    )
                  ) : null}
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="no-selection">
            <p>Select a template from the list to edit</p>
          </div>
        )}
      </div>
    </div>
  );
}

export default TemplateEditor;
