interface TraitSliderProps {
  id: string;
  label: string;
  value: number;
  elasticity: number;
  onChange: (value: number) => void;
  onElasticityChange: (value: number) => void;
  showElasticity?: boolean;
}

export function TraitSlider({
  id,
  label,
  value,
  elasticity,
  onChange,
  onElasticityChange,
  showElasticity = true,
}: TraitSliderProps) {
  const minValue = Math.max(0, value - elasticity);
  const maxValue = Math.min(1, value + elasticity);

  return (
    <div className="pm-trait">
      <div className="pm-trait__header">
        <label className="pm-trait__label" htmlFor={id}>
          {label}
        </label>
        {showElasticity && (
          <span className="pm-trait__elasticity-badge">±{Math.round(elasticity * 100)}%</span>
        )}
      </div>
      <div className="pm-trait__slider-wrap">
        {showElasticity && (
          <div
            className="pm-trait__range-indicator"
            style={{
              left: `${minValue * 100}%`,
              width: `${(maxValue - minValue) * 100}%`,
            }}
          />
        )}
        <input
          type="range"
          id={id}
          className="pm-trait__slider"
          min="0"
          max="100"
          value={Math.round(value * 100)}
          onChange={(e) => onChange(parseInt(e.target.value) / 100)}
        />
        <span className="pm-trait__value">{Math.round(value * 100)}%</span>
      </div>
      {showElasticity && (
        <div className="pm-trait__elasticity-row">
          <span className="pm-trait__elasticity-label">Elasticity</span>
          <input
            type="range"
            className="pm-trait__elasticity-slider"
            min="0"
            max="100"
            value={Math.round(elasticity * 100)}
            onChange={(e) => onElasticityChange(parseInt(e.target.value) / 100)}
          />
          <span className="pm-trait__elasticity-value">{Math.round(elasticity * 100)}%</span>
        </div>
      )}
    </div>
  );
}
