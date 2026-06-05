import type { Dispatch, SetStateAction } from 'react';
import type { NewPricing } from './types';
import { TEXT_UNITS, IMAGE_UNITS, TEXT_UNIT_LABELS, IMAGE_UNIT_LABELS } from './pricingUtils';

interface AddPricingModalProps {
  newPricing: NewPricing;
  setNewPricing: Dispatch<SetStateAction<NewPricing>>;
  providers: string[];
  onCancel: () => void;
  onAdd: () => void;
}

export function AddPricingModal({
  newPricing,
  setNewPricing,
  providers,
  onCancel,
  onAdd,
}: AddPricingModalProps) {
  return (
    <div className="prm-modal-overlay" onClick={onCancel}>
      <div className="prm-modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="prm-modal__title">Add Pricing Entry</h3>

        <div className="prm-form">
          <div className="prm-form__group">
            <label>Provider *</label>
            <input
              type="text"
              className="prm-input"
              value={newPricing.provider}
              onChange={(e) => setNewPricing((p) => ({ ...p, provider: e.target.value }))}
              placeholder="e.g., openai"
              list="providers-list"
            />
            <datalist id="providers-list">
              {providers.map((p) => (
                <option key={p} value={p} />
              ))}
            </datalist>
          </div>

          <div className="prm-form__group">
            <label>Model *</label>
            <input
              type="text"
              className="prm-input"
              value={newPricing.model}
              onChange={(e) => setNewPricing((p) => ({ ...p, model: e.target.value }))}
              placeholder="e.g., gpt-4o"
            />
          </div>

          <div className="prm-form__group">
            <label>Unit *</label>
            <select
              className="prm-select"
              value={newPricing.unit}
              onChange={(e) => setNewPricing((p) => ({ ...p, unit: e.target.value }))}
            >
              <optgroup label="Text Models">
                {TEXT_UNITS.map((unit) => (
                  <option key={unit} value={unit}>
                    {TEXT_UNIT_LABELS[unit]}
                  </option>
                ))}
              </optgroup>
              <optgroup label="Image Models">
                {IMAGE_UNITS.map((unit) => (
                  <option key={unit} value={unit}>
                    {IMAGE_UNIT_LABELS[unit]}
                  </option>
                ))}
              </optgroup>
            </select>
          </div>

          <div className="prm-form__group">
            <label>Cost (USD) *</label>
            <input
              type="number"
              className="prm-input"
              value={newPricing.cost}
              onChange={(e) => setNewPricing((p) => ({ ...p, cost: e.target.value }))}
              placeholder="e.g., 2.50"
              step="0.001"
              min="0"
            />
          </div>

          <div className="prm-form__group">
            <label>Notes</label>
            <input
              type="text"
              className="prm-input"
              value={newPricing.notes}
              onChange={(e) => setNewPricing((p) => ({ ...p, notes: e.target.value }))}
              placeholder="Optional notes"
            />
          </div>
        </div>

        <div className="prm-modal__actions">
          <button className="prm-btn prm-btn--ghost" onClick={onCancel}>
            Cancel
          </button>
          <button className="prm-btn prm-btn--primary" onClick={onAdd}>
            Add Entry
          </button>
        </div>
      </div>
    </div>
  );
}
