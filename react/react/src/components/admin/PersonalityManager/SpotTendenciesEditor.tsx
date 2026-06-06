import { REGISTERED_SPOT_TENDENCIES, MAX_SPOT_TENDENCIES, type SpotTendency } from './types';

interface SpotTendenciesEditorProps {
  value: SpotTendency[];
  onChange: (next: SpotTendency[]) => void;
}

/**
 * Editor for the exploitable-habit texture layer. Each entry is a registered
 * tendency name + a strength (0..1). Names outside the registry are dropped
 * server-side, so only the 9 registered names are offered, capped at 3 and
 * deduped (a name already used in another row is disabled).
 */
export function SpotTendenciesEditor({ value, onChange }: SpotTendenciesEditorProps) {
  const tendencies = value ?? [];
  const usedNames = new Set(tendencies.map(([name]) => name));
  const canAdd = tendencies.length < MAX_SPOT_TENDENCIES;

  const updateName = (index: number, name: string) => {
    onChange(tendencies.map((t, i) => (i === index ? [name, t[1]] : t)));
  };

  const updateStrength = (index: number, strength: number) => {
    onChange(tendencies.map((t, i) => (i === index ? [t[0], strength] : t)));
  };

  const removeRow = (index: number) => {
    onChange(tendencies.filter((_, i) => i !== index));
  };

  const addRow = () => {
    const firstUnused =
      REGISTERED_SPOT_TENDENCIES.find((t) => !usedNames.has(t.value))?.value ??
      REGISTERED_SPOT_TENDENCIES[0].value;
    onChange([...tendencies, [firstUnused, 0.5]]);
  };

  return (
    <div className="pm-spot">
      <p className="admin-help-text" style={{ marginTop: 0 }}>
        Deliberate, exploitable habits layered on top of the base strategy. Used by the sharp /
        tiered bot to give a persona readable texture. Up to {MAX_SPOT_TENDENCIES}.
      </p>

      {tendencies.map(([name, strength], index) => {
        const hint = REGISTERED_SPOT_TENDENCIES.find((t) => t.value === name)?.hint ?? '';
        return (
          <div key={index} className="pm-spot__entry">
            <div className="pm-spot__row">
              <select
                className="admin-input admin-select pm-spot__name"
                value={name}
                onChange={(e) => updateName(index, e.target.value)}
              >
                {REGISTERED_SPOT_TENDENCIES.map((option) => (
                  <option
                    key={option.value}
                    value={option.value}
                    disabled={option.value !== name && usedNames.has(option.value)}
                  >
                    {option.label}
                  </option>
                ))}
              </select>
              <div className="pm-spot__strength">
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={Math.round(strength * 100)}
                  onChange={(e) => updateStrength(index, parseInt(e.target.value) / 100)}
                />
                <span className="pm-spot__strength-value">{Math.round(strength * 100)}%</span>
              </div>
              <button
                type="button"
                className="pm-array__remove"
                onClick={() => removeRow(index)}
                aria-label="Remove tendency"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path
                    d="M4 4L12 12M12 4L4 12"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                  />
                </svg>
              </button>
            </div>
            {hint && <p className="admin-help-text pm-spot__hint">{hint}</p>}
          </div>
        );
      })}

      {tendencies.length === 0 && (
        <p className="admin-help-text">
          No tendencies set — this persona plays strategically generic.
        </p>
      )}

      <button
        type="button"
        className="pm-array__add"
        onClick={addRow}
        disabled={!canAdd}
        title={canAdd ? undefined : `Maximum ${MAX_SPOT_TENDENCIES} tendencies`}
      >
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M7 1V13M1 7H13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
        Add Tendency
      </button>
    </div>
  );
}
