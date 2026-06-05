interface ArrayInputProps {
  label: string;
  items: string[];
  onChange: (items: string[]) => void;
  placeholder?: string;
}

export function ArrayInput({ label, items, onChange, placeholder }: ArrayInputProps) {
  const handleItemChange = (index: number, value: string) => {
    const newItems = [...items];
    newItems[index] = value;
    onChange(newItems);
  };

  const handleRemove = (index: number) => {
    onChange(items.filter((_, i) => i !== index));
  };

  const handleAdd = () => {
    onChange([...items, '']);
  };

  return (
    <div className="pm-array">
      <label className="admin-label">{label}</label>
      <div className="pm-array__items">
        {items.map((item, index) => (
          <div key={index} className="pm-array__item">
            <input
              type="text"
              className="pm-array__input"
              value={item}
              onChange={(e) => handleItemChange(index, e.target.value)}
              placeholder={placeholder}
            />
            <button
              type="button"
              className="pm-array__remove"
              onClick={() => handleRemove(index)}
              aria-label="Remove item"
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
        ))}
      </div>
      <button type="button" className="pm-array__add" onClick={handleAdd}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M7 1V13M1 7H13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
        Add {label.replace(/s$/, '')}
      </button>
    </div>
  );
}
