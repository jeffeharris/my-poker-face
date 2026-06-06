import { useEffect, useRef, useState } from 'react';

interface CreateModalProps {
  onCreateManual: (name: string) => void;
  onCreateWithAI: (name: string) => void;
  onCancel: () => void;
  existingNames: string[];
  isLoading?: boolean;
}

export function CreateModal({
  onCreateManual,
  onCreateWithAI,
  onCancel,
  existingNames,
  isLoading,
}: CreateModalProps) {
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSubmit = (useAI: boolean) => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError('Please enter a name');
      return;
    }
    if (existingNames.includes(trimmedName)) {
      setError('A personality with this name already exists');
      return;
    }
    if (useAI) {
      onCreateWithAI(trimmedName);
    } else {
      onCreateManual(trimmedName);
    }
  };

  return (
    <div className="admin-modal-overlay" onClick={onCancel}>
      <div className="admin-modal pm-modal--create" onClick={(e) => e.stopPropagation()}>
        <div className="admin-modal__header">
          <h3 className="admin-modal__title">Create New Personality</h3>
        </div>
        <div className="admin-modal__body">
          <div className="admin-form-group">
            <label className="admin-label" htmlFor="new-personality-name">
              Character Name
            </label>
            <input
              ref={inputRef}
              id="new-personality-name"
              type="text"
              className={`admin-input ${error ? 'admin-input--error' : ''}`}
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setError('');
              }}
              placeholder="e.g., Batman, The Rock, Marie Curie..."
              disabled={isLoading}
            />
            {error && (
              <span
                className="admin-text-error"
                style={{ fontSize: 'var(--font-size-sm)', marginTop: 'var(--space-1)' }}
              >
                {error}
              </span>
            )}
          </div>
          <div className="pm-modal__create-actions">
            <button
              type="button"
              className="pm-modal__create-btn pm-modal__create-btn--ai"
              onClick={() => handleSubmit(true)}
              disabled={isLoading || !name.trim()}
            >
              <span className="pm-modal__create-icon">✨</span>
              <span className="pm-modal__create-text">
                <strong>Generate with AI</strong>
                <small>Auto-create personality traits</small>
              </span>
            </button>
            <button
              type="button"
              className="pm-modal__create-btn pm-modal__create-btn--manual"
              onClick={() => handleSubmit(false)}
              disabled={isLoading || !name.trim()}
            >
              <span className="pm-modal__create-icon">✏️</span>
              <span className="pm-modal__create-text">
                <strong>Create Manually</strong>
                <small>Start with default values</small>
              </span>
            </button>
          </div>
        </div>
        <div className="admin-modal__footer">
          <button
            type="button"
            className="admin-btn admin-btn--secondary"
            onClick={onCancel}
            disabled={isLoading}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
