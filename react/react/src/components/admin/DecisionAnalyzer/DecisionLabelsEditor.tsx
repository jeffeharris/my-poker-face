import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { X, Plus, Tag } from 'lucide-react';
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
  // Labels already known from the list payload; rendered immediately, then
  // refreshed from the server.
  initialLabels?: DecisionLabel[];
  // Notify the parent so the list row can re-sync its label pills.
  onLabelsChanged?: (labels: DecisionLabel[]) => void;
}

type Option = { kind: 'existing'; value: string } | { kind: 'create'; value: string };

// Tag/flag editor for a single decision. Works for EVERY player type (human,
// tiered, rule, LLM) because labels are keyed on the decision spine, not the
// LLM capture. Combobox UX: type to filter existing labels, pick one or create
// a new one. Mobile-first tap targets. POSTs to /api/decisions/<id>/labels.
export function DecisionLabelsEditor({
  decisionId,
  initialLabels = [],
  onLabelsChanged,
}: DecisionLabelsEditorProps) {
  const [labels, setLabels] = useState<DecisionLabel[]>(initialLabels);
  const [available, setAvailable] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const [busy, setBusy] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const applied = useMemo(() => new Set(labels.map((l) => l.label)), [labels]);

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

  const loadAvailable = useCallback(async () => {
    try {
      const res = await fetch(`${config.API_URL}/api/capture-labels`, { credentials: 'include' });
      const data = await res.json();
      if (data.success) {
        setAvailable((data.labels as Array<{ name: string }>).map((l) => l.name));
      }
    } catch {
      // suggestions are best-effort
    }
  }, []);

  useEffect(() => {
    setLabels(initialLabels);
    refresh();
    // initialLabels is a fresh array each render; key off decisionId only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [decisionId]);

  useEffect(() => {
    loadAvailable();
  }, [loadAvailable]);

  // Close the dropdown on outside click/tap.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent | TouchEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('touchstart', onDown);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('touchstart', onDown);
    };
  }, [open]);

  const q = query.trim().toLowerCase();
  const options = useMemo<Option[]>(() => {
    const matches = available
      .filter((name) => !applied.has(name))
      .filter((name) => q === '' || name.toLowerCase().includes(q))
      .slice(0, 8)
      .map((value) => ({ kind: 'existing' as const, value }));
    const exact = available.some((name) => name.toLowerCase() === q);
    const showCreate = q !== '' && !exact && !applied.has(q);
    return showCreate ? [...matches, { kind: 'create', value: query.trim() }] : matches;
  }, [available, applied, q, query]);

  useEffect(() => {
    setHighlight(0);
  }, [query]);

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
          if (body.add?.length) loadAvailable();
        }
      } catch (e) {
        logger.error('Failed to update decision labels', e);
      } finally {
        setBusy(false);
      }
    },
    [decisionId, onLabelsChanged, loadAvailable]
  );

  const addLabel = (value: string) => {
    const v = value.trim().toLowerCase();
    if (!v || applied.has(v)) {
      setQuery('');
      return;
    }
    setQuery('');
    setOpen(false);
    mutate({ add: [v] });
  };

  const choose = (opt: Option) => addLabel(opt.value);

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setOpen(true);
      setHighlight((h) => Math.min(h + 1, Math.max(options.length - 1, 0)));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (options[highlight]) choose(options[highlight]);
      else if (query.trim()) addLabel(query);
    } else if (e.key === 'Escape') {
      setOpen(false);
    } else if (e.key === 'Backspace' && query === '' && labels.length > 0) {
      mutate({ remove: [labels[labels.length - 1].label] });
    }
  };

  return (
    <div className="dle" ref={containerRef}>
      <div className="dle__header">
        <Tag size={13} />
        <span>Flags &amp; labels</span>
      </div>

      <div className="dle__pills">
        {labels.length === 0 && <span className="dle__empty">No flags yet</span>}
        {labels.map((l) => (
          <span
            key={l.label}
            className={`capture-label capture-label--${getLabelSeverity(l.label)} capture-label--${l.label_type} dle__chip`}
            title={l.label_type === 'auto' ? 'Auto-generated' : 'Added by you'}
          >
            {formatLabelName(l.label)}
            <button
              type="button"
              className="dle__chip-remove"
              disabled={busy}
              onClick={() => mutate({ remove: [l.label] })}
              aria-label={`Remove ${l.label}`}
            >
              <X size={11} />
            </button>
          </span>
        ))}
      </div>

      <div className="dle__combo">
        <input
          type="text"
          className="dle__input"
          placeholder="Type to flag… (e.g. mistake, cooler)"
          value={query}
          disabled={busy}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onKeyDown={onKeyDown}
        />

        {open && options.length > 0 && (
          <ul className="dle__options" role="listbox">
            {options.map((opt, i) => (
              <li
                key={`${opt.kind}:${opt.value}`}
                role="option"
                aria-selected={i === highlight}
                className={`dle__option ${i === highlight ? 'dle__option--active' : ''} ${
                  opt.kind === 'create' ? 'dle__option--create' : ''
                }`}
                // onMouseDown (not onClick) so it fires before the input blur.
                onMouseDown={(e) => {
                  e.preventDefault();
                  choose(opt);
                }}
                onMouseEnter={() => setHighlight(i)}
              >
                {opt.kind === 'create' ? (
                  <>
                    <Plus size={13} />
                    <span>
                      Create “<strong>{opt.value}</strong>”
                    </span>
                  </>
                ) : (
                  <span>{formatLabelName(opt.value)}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
