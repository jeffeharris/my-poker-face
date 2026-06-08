import { useState, useEffect, useCallback } from 'react';
import { X } from 'lucide-react';
import { config } from '../../../config';
import { logger } from '../../../utils/logger';
import { formatLabelName, getLabelSeverity } from './utils';

interface DecisionLabel {
  label: string;
  label_type: string;
  created_at?: string;
}

interface DecisionLabelsEditorProps {
  decisionId: number;
  // Labels already known from the list payload; used as the initial render so
  // the editor paints instantly, then refreshed from the server.
  initialLabels?: DecisionLabel[];
  // Notify the parent so the list row can re-sync its label pills.
  onLabelsChanged?: (labels: DecisionLabel[]) => void;
}

// Tag editor for a single decision. Works for EVERY player type (human, tiered,
// rule, LLM) because labels are keyed on the decision spine, not the LLM
// capture. POSTs to /api/decisions/<id>/labels.
export function DecisionLabelsEditor({
  decisionId,
  initialLabels = [],
  onLabelsChanged,
}: DecisionLabelsEditorProps) {
  const [labels, setLabels] = useState<DecisionLabel[]>(initialLabels);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${config.API_URL}/api/decisions/${decisionId}/labels`, {
        credentials: 'include',
      });
      const data = await res.json();
      if (data.success) {
        setLabels(data.labels);
        onLabelsChanged?.(data.labels);
      }
    } catch (e) {
      logger.error('Failed to load decision labels', e);
    }
  }, [decisionId, onLabelsChanged]);

  useEffect(() => {
    setLabels(initialLabels);
    refresh();
    // initialLabels is a fresh array each render; key off decisionId only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [decisionId]);

  const mutate = useCallback(
    async (body: { add?: string[]; remove?: string[] }) => {
      setBusy(true);
      try {
        const res = await fetch(`${config.API_URL}/api/decisions/${decisionId}/labels`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.success) {
          setLabels(data.labels);
          onLabelsChanged?.(data.labels);
        }
      } catch (e) {
        logger.error('Failed to update decision labels', e);
      } finally {
        setBusy(false);
      }
    },
    [decisionId, onLabelsChanged]
  );

  const addLabel = () => {
    const value = input.trim().toLowerCase();
    if (!value) return;
    setInput('');
    if (labels.some((l) => l.label === value)) return;
    mutate({ add: [value] });
  };

  return (
    <div className="decision-labels-editor">
      <div className="decision-labels-editor__header">Labels</div>
      <div className="decision-labels-editor__pills">
        {labels.length === 0 && <span className="decision-labels-editor__empty">No labels</span>}
        {labels.map((l) => (
          <span
            key={l.label}
            className={`capture-label capture-label--${getLabelSeverity(l.label)} capture-label--${l.label_type}`}
          >
            {formatLabelName(l.label)}
            <button
              type="button"
              className="decision-labels-editor__remove"
              disabled={busy}
              onClick={() => mutate({ remove: [l.label] })}
              aria-label={`Remove label ${l.label}`}
            >
              <X size={10} />
            </button>
          </span>
        ))}
      </div>
      <input
        type="text"
        className="decision-labels-editor__input"
        placeholder="Add label, press Enter…"
        value={input}
        disabled={busy}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            addLabel();
          }
        }}
      />
    </div>
  );
}
