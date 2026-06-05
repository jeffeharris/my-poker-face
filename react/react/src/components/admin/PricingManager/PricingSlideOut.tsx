import { useState, useEffect, useMemo, useImperativeHandle, forwardRef } from 'react';
import type { PivotedModel, SlideOutRef } from './types';
import { getTodayISO } from './pricingUtils';

interface SlideOutProps {
  model: PivotedModel;
  units: readonly string[];
  unitLabels: Record<string, string>;
  onClose: () => void;
  onSave: (values: Record<string, string>, validFrom: string) => Promise<void>;
  saving: boolean;
  pendingValues?: { values: Record<string, string>; validFrom: string } | null;
}

export const PricingSlideOut = forwardRef<SlideOutRef, SlideOutProps>(function PricingSlideOut(
  { model, units, unitLabels, onClose, onSave, saving, pendingValues },
  ref
) {
  const [editValues, setEditValues] = useState<Record<string, string>>({});
  const [validFrom, setValidFrom] = useState(getTodayISO());

  // Reset state when model changes
  useEffect(() => {
    if (pendingValues) {
      setEditValues({ ...pendingValues.values });
      setValidFrom(pendingValues.validFrom);
    } else {
      const initial: Record<string, string> = {};
      for (const unit of units) {
        const cost = model.costs[unit];
        initial[unit] = cost !== null && cost !== undefined ? cost.toString() : '';
      }
      setEditValues(initial);
      setValidFrom(getTodayISO());
    }
  }, [model.provider, model.model, model.costs, pendingValues, units]);

  const isDirty = useMemo(() => {
    for (const unit of units) {
      const original = model.costs[unit];
      const current = editValues[unit];
      const originalStr = original !== null && original !== undefined ? original.toString() : '';
      if (current !== originalStr) return true;
    }
    return false;
  }, [editValues, model.costs, units]);

  // Expose methods to parent via ref
  useImperativeHandle(
    ref,
    () => ({
      isDirty: () => isDirty,
      getValues: () => ({ values: editValues, validFrom }),
    }),
    [isDirty, editValues, validFrom]
  );

  const handleSave = () => {
    onSave(editValues, validFrom);
  };

  return (
    <>
      <div className="prm-slideout-backdrop" onClick={onClose} />
      <div className="prm-slideout">
        <div className="prm-slideout__header">
          <div className="prm-slideout__title-row">
            <span className="prm-slideout__provider-badge">{model.provider}</span>
            <span className="prm-slideout__model-name">{model.model}</span>
          </div>
          <button className="prm-slideout__close" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="prm-slideout__content">
          <div className="prm-form__group">
            <label>Valid From</label>
            <input
              type="date"
              className="prm-input"
              value={validFrom}
              onChange={(e) => setValidFrom(e.target.value)}
            />
          </div>

          <div className="prm-slideout__divider" />

          {units.map((unit) => (
            <div key={unit} className="prm-form__group">
              <label>{unitLabels[unit]}</label>
              <input
                type="number"
                className="prm-input"
                value={editValues[unit]}
                onChange={(e) => setEditValues((prev) => ({ ...prev, [unit]: e.target.value }))}
                onKeyDown={(e) => {
                  if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
                    e.preventDefault();
                  }
                }}
                placeholder="Not set"
                step="0.01"
                min="0"
              />
            </div>
          ))}
        </div>

        <div className="prm-slideout__footer">
          <div className="prm-slideout__hint">↑↓ Navigate models</div>
          <div className="prm-slideout__actions">
            <button className="prm-btn prm-btn--ghost" onClick={onClose} disabled={saving}>
              Cancel
            </button>
            <button
              className="prm-btn prm-btn--primary"
              onClick={handleSave}
              disabled={!isDirty || saving}
            >
              {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
        </div>
      </div>
    </>
  );
});
