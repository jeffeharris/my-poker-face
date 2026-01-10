import { useState, useEffect, useCallback, useRef } from 'react';
import { config } from '../../config';
import { PageLayout, PageHeader } from '../shared';
import './PersonalityManager.css';

// ============================================
// Types
// ============================================

interface PersonalityTraits {
  bluff_tendency: number;
  aggression: number;
  chattiness: number;
  emoji_usage: number;
}

interface ElasticityConfig {
  trait_elasticity: PersonalityTraits;
  mood_elasticity: number;
  recovery_rate: number;
}

interface PersonalityData {
  play_style?: string;
  default_confidence?: string;
  default_attitude?: string;
  personality_traits?: PersonalityTraits;
  elasticity_config?: ElasticityConfig;
  verbal_tics?: string[];
  physical_tics?: string[];
  avatar_description?: string;
}

interface EmotionImage {
  emotion: string;
  url: string | null;
  hasFullImage: boolean;
}

interface AlertState {
  type: 'success' | 'error' | 'info';
  message: string;
}

interface ModalState {
  type: 'delete' | 'regenerate' | 'create' | null;
  data?: unknown;
}

// ============================================
// Sub-components
// ============================================

interface CollapsibleSectionProps {
  title: string;
  icon: string;
  isOpen: boolean;
  onToggle: () => void;
  children: React.ReactNode;
  badge?: string;
}

function CollapsibleSection({ title, icon, isOpen, onToggle, children, badge }: CollapsibleSectionProps) {
  const contentRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number | undefined>(undefined);

  useEffect(() => {
    if (contentRef.current) {
      setHeight(isOpen ? contentRef.current.scrollHeight : 0);
    }
  }, [isOpen, children]);

  return (
    <div className={`pm-section ${isOpen ? 'pm-section--open' : ''}`}>
      <button className="pm-section__header" onClick={onToggle} type="button">
        <span className="pm-section__icon">{icon}</span>
        <span className="pm-section__title">{title}</span>
        {badge && <span className="pm-section__badge">{badge}</span>}
        <span className="pm-section__chevron">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path d="M5 7.5L10 12.5L15 7.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </span>
      </button>
      <div
        className="pm-section__content"
        style={{ height: height !== undefined ? `${height}px` : 'auto' }}
      >
        <div ref={contentRef} className="pm-section__inner">
          {children}
        </div>
      </div>
    </div>
  );
}

interface TraitSliderProps {
  id: string;
  label: string;
  value: number;
  elasticity: number;
  onChange: (value: number) => void;
  onElasticityChange: (value: number) => void;
  showElasticity?: boolean;
}

function TraitSlider({ id, label, value, elasticity, onChange, onElasticityChange, showElasticity = true }: TraitSliderProps) {
  const minValue = Math.max(0, value - elasticity);
  const maxValue = Math.min(1, value + elasticity);

  return (
    <div className="pm-trait">
      <div className="pm-trait__header">
        <label className="pm-trait__label" htmlFor={id}>{label}</label>
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
              width: `${(maxValue - minValue) * 100}%`
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

interface ArrayInputProps {
  label: string;
  items: string[];
  onChange: (items: string[]) => void;
  placeholder?: string;
}

function ArrayInput({ label, items, onChange, placeholder }: ArrayInputProps) {
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
      <label className="pm-array__label">{label}</label>
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
                <path d="M4 4L12 12M12 4L4 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              </svg>
            </button>
          </div>
        ))}
      </div>
      <button type="button" className="pm-array__add" onClick={handleAdd}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M7 1V13M1 7H13" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
        </svg>
        Add {label.replace(/s$/, '')}
      </button>
    </div>
  );
}

interface ConfirmModalProps {
  title: string;
  message: string;
  confirmLabel: string;
  confirmVariant?: 'danger' | 'warning' | 'primary';
  onConfirm: () => void;
  onCancel: () => void;
  isLoading?: boolean;
}

function ConfirmModal({ title, message, confirmLabel, confirmVariant = 'primary', onConfirm, onCancel, isLoading }: ConfirmModalProps) {
  return (
    <div className="pm-modal-overlay" onClick={onCancel}>
      <div className="pm-modal" onClick={e => e.stopPropagation()}>
        <h3 className="pm-modal__title">{title}</h3>
        <p className="pm-modal__message">{message}</p>
        <div className="pm-modal__actions">
          <button type="button" className="pm-modal__btn pm-modal__btn--cancel" onClick={onCancel} disabled={isLoading}>
            Cancel
          </button>
          <button
            type="button"
            className={`pm-modal__btn pm-modal__btn--${confirmVariant}`}
            onClick={onConfirm}
            disabled={isLoading}
          >
            {isLoading ? 'Processing...' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

interface CreateModalProps {
  onCreateManual: (name: string) => void;
  onCreateWithAI: (name: string) => void;
  onCancel: () => void;
  existingNames: string[];
  isLoading?: boolean;
}

function CreateModal({ onCreateManual, onCreateWithAI, onCancel, existingNames, isLoading }: CreateModalProps) {
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
    <div className="pm-modal-overlay" onClick={onCancel}>
      <div className="pm-modal pm-modal--create" onClick={e => e.stopPropagation()}>
        <h3 className="pm-modal__title">Create New Personality</h3>
        <div className="pm-modal__field">
          <label className="pm-modal__label" htmlFor="new-personality-name">Character Name</label>
          <input
            ref={inputRef}
            id="new-personality-name"
            type="text"
            className="pm-modal__input"
            value={name}
            onChange={(e) => { setName(e.target.value); setError(''); }}
            placeholder="e.g., Batman, The Rock, Marie Curie..."
            disabled={isLoading}
          />
          {error && <span className="pm-modal__error">{error}</span>}
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
        <button type="button" className="pm-modal__close" onClick={onCancel} disabled={isLoading}>
          Cancel
        </button>
      </div>
    </div>
  );
}

interface CharacterSelectorProps {
  characters: string[];
  selected: string | null;
  onSelect: (name: string) => void;
  onCreate: () => void;
  isOpen: boolean;
  onClose: () => void;
}

function CharacterSelector({ characters, selected, onSelect, onCreate, isOpen, onClose }: CharacterSelectorProps) {
  const [search, setSearch] = useState('');

  const filtered = characters.filter(name =>
    name.toLowerCase().includes(search.toLowerCase())
  );

  const handleSelect = (name: string) => {
    onSelect(name);
    onClose();
  };

  return (
    <>
      <div className={`pm-sheet-backdrop ${isOpen ? 'pm-sheet-backdrop--visible' : ''}`} onClick={onClose} />
      <div className={`pm-sheet ${isOpen ? 'pm-sheet--open' : ''}`}>
        <div className="pm-sheet__handle" onClick={onClose}>
          <div className="pm-sheet__handle-bar" />
        </div>
        <div className="pm-sheet__header">
          <h3 className="pm-sheet__title">Select Character</h3>
          <span className="pm-sheet__count">{characters.length} personalities</span>
        </div>
        <div className="pm-sheet__search">
          <svg className="pm-sheet__search-icon" width="18" height="18" viewBox="0 0 18 18" fill="none">
            <circle cx="8" cy="8" r="5.5" stroke="currentColor" strokeWidth="1.5"/>
            <path d="M12 12L16 16" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          <input
            type="text"
            className="pm-sheet__search-input"
            placeholder="Search characters..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="pm-sheet__list">
          {filtered.map((name, index) => (
            <button
              key={name}
              type="button"
              className={`pm-sheet__item ${selected === name ? 'pm-sheet__item--active' : ''}`}
              onClick={() => handleSelect(name)}
              style={{ animationDelay: `${index * 20}ms` }}
            >
              <span className="pm-sheet__item-avatar">{name.charAt(0)}</span>
              <span className="pm-sheet__item-name">{name}</span>
              {selected === name && (
                <svg className="pm-sheet__item-check" width="18" height="18" viewBox="0 0 18 18" fill="none">
                  <path d="M4 9L7.5 12.5L14 5.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              )}
            </button>
          ))}
          {filtered.length === 0 && (
            <div className="pm-sheet__empty">
              No characters found matching "{search}"
            </div>
          )}
        </div>
        <button type="button" className="pm-sheet__create" onClick={onCreate}>
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
            <path d="M9 3V15M3 9H15" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
          </svg>
          Create New Character
        </button>
      </div>
    </>
  );
}

interface AvatarImageManagerProps {
  personalityName: string;
  avatarDescription: string;
  onDescriptionChange: (desc: string) => void;
  onDescriptionSave: () => Promise<void>;
}

function AvatarImageManager({ personalityName, avatarDescription, onDescriptionChange, onDescriptionSave }: AvatarImageManagerProps) {
  const [images, setImages] = useState<EmotionImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [regenerating, setRegenerating] = useState<string | null>(null);
  const [savingDescription, setSavingDescription] = useState(false);

  useEffect(() => {
    loadEmotionsAndImages();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [personalityName]);

  const loadEmotionsAndImages = async () => {
    setLoading(true);
    try {
      // Fetch available emotions
      const emotionsRes = await fetch(`${config.API_URL}/api/avatar/emotions`);
      const emotionsData = await emotionsRes.json();
      const emotionsList = emotionsData.emotions || ['confident', 'happy', 'thinking', 'nervous', 'angry', 'shocked'];

      // Check which images exist - use full/square images for the manager
      const imagePromises = emotionsList.map(async (emotion: string) => {
        try {
          const fullRes = await fetch(`${config.API_URL}/api/avatar/${encodeURIComponent(personalityName)}/${emotion}/full`, { method: 'HEAD' });
          return {
            emotion,
            // Use the full/square image if available
            url: fullRes.ok ? `${config.API_URL}/api/avatar/${encodeURIComponent(personalityName)}/${emotion}/full` : null,
            hasFullImage: fullRes.ok
          };
        } catch {
          return { emotion, url: null, hasFullImage: false };
        }
      });

      const imageResults = await Promise.all(imagePromises);
      setImages(imageResults);
    } catch (error) {
      console.error('Failed to load avatar images:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleRegenerate = async (emotion: string) => {
    setRegenerating(emotion);
    try {
      const res = await fetch(`${config.API_URL}/api/avatar/${encodeURIComponent(personalityName)}/regenerate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ emotions: [emotion] })
      });
      const data = await res.json();
      if (data.success) {
        // Update avatar description if it was auto-generated
        if (data.avatar_description && data.avatar_description !== avatarDescription) {
          onDescriptionChange(data.avatar_description);
        }
        await loadEmotionsAndImages();
      }
    } catch (error) {
      console.error('Failed to regenerate:', error);
    } finally {
      setRegenerating(null);
    }
  };

  const handleGenerateMissing = async () => {
    const missing = images.filter(img => !img.url).map(img => img.emotion);
    if (missing.length === 0) return;

    setRegenerating('all');
    try {
      const res = await fetch(`${config.API_URL}/api/avatar/${encodeURIComponent(personalityName)}/regenerate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ emotions: missing })
      });
      const data = await res.json();
      // Update avatar description if it was auto-generated
      if (data.avatar_description && data.avatar_description !== avatarDescription) {
        onDescriptionChange(data.avatar_description);
      }
      await loadEmotionsAndImages();
    } catch (error) {
      console.error('Failed to generate missing:', error);
    } finally {
      setRegenerating(null);
    }
  };

  const handleSaveDescription = async () => {
    setSavingDescription(true);
    try {
      await onDescriptionSave();
    } finally {
      setSavingDescription(false);
    }
  };

  const missingCount = images.filter(img => !img.url).length;

  if (loading) {
    return (
      <div className="pm-avatar__loading">
        <div className="pm-avatar__spinner" />
        <span>Loading images...</span>
      </div>
    );
  }

  return (
    <div className="pm-avatar">
      <div className="pm-avatar__description">
        <label className="pm-avatar__desc-label" htmlFor="avatar-desc">
          Image Description
          <span className="pm-avatar__desc-hint">Used for AI image generation</span>
        </label>
        <textarea
          id="avatar-desc"
          className="pm-avatar__desc-input"
          value={avatarDescription}
          onChange={(e) => onDescriptionChange(e.target.value)}
          placeholder="Describe this character's appearance for image generation..."
          rows={3}
        />
        <button
          type="button"
          className="pm-avatar__desc-save"
          onClick={handleSaveDescription}
          disabled={savingDescription}
        >
          {savingDescription ? 'Saving...' : 'Save Description'}
        </button>
      </div>

      <div className="pm-avatar__grid">
        {images.map(({ emotion, url }) => (
          <div key={emotion} className="pm-avatar__card">
            <div className="pm-avatar__image-wrap">
              {url ? (
                <img
                  src={url}
                  alt={`${personalityName} - ${emotion}`}
                  className="pm-avatar__image"
                />
              ) : (
                <div className="pm-avatar__placeholder">
                  <span>?</span>
                </div>
              )}
              {regenerating === emotion && (
                <div className="pm-avatar__regenerating">
                  <div className="pm-avatar__spinner pm-avatar__spinner--small" />
                </div>
              )}
            </div>
            <div className="pm-avatar__card-footer">
              <span className="pm-avatar__emotion">{emotion}</span>
              <button
                type="button"
                className="pm-avatar__refresh"
                onClick={() => handleRegenerate(emotion)}
                disabled={regenerating !== null}
                title={`Regenerate ${emotion}`}
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <path d="M1 7C1 3.686 3.686 1 7 1C9.21 1 11.117 2.214 12.143 4M13 7C13 10.314 10.314 13 7 13C4.79 13 2.883 11.786 1.857 10M12.143 4V1M12.143 4H9.143M1.857 10V13M1.857 10H4.857" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>
            </div>
          </div>
        ))}
      </div>

      {missingCount > 0 && (
        <button
          type="button"
          className="pm-avatar__generate-missing"
          onClick={handleGenerateMissing}
          disabled={regenerating !== null}
        >
          {regenerating === 'all' ? (
            <>
              <div className="pm-avatar__spinner pm-avatar__spinner--small" />
              Generating...
            </>
          ) : (
            <>
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path d="M8 1V15M1 8H15" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              </svg>
              Generate {missingCount} Missing Image{missingCount > 1 ? 's' : ''}
            </>
          )}
        </button>
      )}
    </div>
  );
}

// ============================================
// Main Component
// ============================================

interface PersonalityManagerProps {
  onBack: () => void;
}

export function PersonalityManager({ onBack }: PersonalityManagerProps) {
  // Core state
  const [personalities, setPersonalities] = useState<Record<string, PersonalityData>>({});
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [formData, setFormData] = useState<PersonalityData | null>(null);
  const [originalData, setOriginalData] = useState<PersonalityData | null>(null);

  // UI state
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [alert, setAlert] = useState<AlertState | null>(null);
  const [modal, setModal] = useState<ModalState>({ type: null });
  const [selectorOpen, setSelectorOpen] = useState(false);

  // Accordion state
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({
    basic: true,
    traits: false,
    elasticity: false,
    tics: false,
    avatar: false
  });

  // Load personalities on mount
  useEffect(() => {
    loadPersonalities();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-dismiss alerts
  useEffect(() => {
    if (alert) {
      const timer = setTimeout(() => setAlert(null), 5000);
      return () => clearTimeout(timer);
    }
  }, [alert]);

  const loadPersonalities = async () => {
    setLoading(true);
    try {
      const response = await fetch(`${config.API_URL}/api/personalities`);
      const data = await response.json();
      if (data.success) {
        setPersonalities(data.personalities);
      } else {
        showAlert('error', 'Failed to load personalities: ' + data.error);
      }
    } catch {
      showAlert('error', 'Error loading personalities');
    } finally {
      setLoading(false);
    }
  };

  const showAlert = (type: AlertState['type'], message: string) => {
    setAlert({ type, message });
  };

  const toggleSection = (section: string) => {
    setOpenSections(prev => ({ ...prev, [section]: !prev[section] }));
  };

  const selectPersonality = useCallback((name: string) => {
    const data = personalities[name];
    if (data) {
      setSelectedName(name);
      setFormData({ ...data });
      setOriginalData({ ...data });
      // Open basic section by default
      setOpenSections(prev => ({ ...prev, basic: true }));
    }
  }, [personalities]);

  const updateFormData = useCallback((updates: Partial<PersonalityData>) => {
    setFormData(prev => prev ? { ...prev, ...updates } : null);
  }, []);

  const updateTraits = useCallback((trait: keyof PersonalityTraits, value: number) => {
    setFormData(prev => {
      if (!prev) return null;
      return {
        ...prev,
        personality_traits: {
          ...getDefaultTraits(),
          ...prev.personality_traits,
          [trait]: value
        }
      };
    });
  }, []);

  const updateElasticity = useCallback((trait: keyof PersonalityTraits, value: number) => {
    setFormData(prev => {
      if (!prev) return null;
      const currentConfig = prev.elasticity_config || getDefaultElasticity();
      return {
        ...prev,
        elasticity_config: {
          ...currentConfig,
          trait_elasticity: {
            ...currentConfig.trait_elasticity,
            [trait]: value
          }
        }
      };
    });
  }, []);

  const updateMoodSettings = useCallback((field: 'mood_elasticity' | 'recovery_rate', value: number) => {
    setFormData(prev => {
      if (!prev) return null;
      const currentConfig = prev.elasticity_config || getDefaultElasticity();
      return {
        ...prev,
        elasticity_config: {
          ...currentConfig,
          [field]: value
        }
      };
    });
  }, []);

  const handleSave = async () => {
    if (!selectedName || !formData) return;

    setSaving(true);
    try {
      const response = await fetch(`${config.API_URL}/api/personality/${selectedName}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData)
      });
      const data = await response.json();

      if (data.success) {
        showAlert('success', `Saved ${selectedName} successfully`);
        setPersonalities(prev => ({ ...prev, [selectedName]: formData }));
        setOriginalData({ ...formData });
      } else {
        showAlert('error', 'Failed to save: ' + data.error);
      }
    } catch {
      showAlert('error', 'Error saving personality');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!selectedName) return;

    setSaving(true);
    try {
      const response = await fetch(`${config.API_URL}/api/personality/${selectedName}`, {
        method: 'DELETE'
      });
      const data = await response.json();

      if (data.success) {
        showAlert('success', `Deleted ${selectedName}`);
        setPersonalities(prev => {
          const next = { ...prev };
          delete next[selectedName];
          return next;
        });
        setSelectedName(null);
        setFormData(null);
        setOriginalData(null);
      } else {
        showAlert('error', 'Failed to delete: ' + data.error);
      }
    } catch {
      showAlert('error', 'Error deleting personality');
    } finally {
      setSaving(false);
      setModal({ type: null });
    }
  };

  const handleRegenerate = async () => {
    if (!selectedName) return;

    setSaving(true);
    showAlert('info', `Regenerating personality for ${selectedName}...`);

    try {
      const response = await fetch(`${config.API_URL}/api/generate_personality`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: selectedName, force: true })
      });
      const data = await response.json();

      if (data.success) {
        showAlert('success', `Regenerated ${selectedName} with AI`);
        setPersonalities(prev => ({ ...prev, [selectedName]: data.personality }));
        setFormData({ ...data.personality });
        setOriginalData({ ...data.personality });
      } else {
        showAlert('error', 'Regeneration failed: ' + (data.message || data.error));
      }
    } catch {
      showAlert('error', 'Error regenerating personality');
    } finally {
      setSaving(false);
      setModal({ type: null });
    }
  };

  const handleCreateManual = (name: string) => {
    const newPersonality: PersonalityData = {
      play_style: 'balanced',
      default_confidence: 'confident',
      default_attitude: 'focused',
      personality_traits: getDefaultTraits(),
      elasticity_config: getDefaultElasticity(),
      verbal_tics: [],
      physical_tics: []
    };

    setPersonalities(prev => ({ ...prev, [name]: newPersonality }));
    setSelectedName(name);
    setFormData({ ...newPersonality });
    setOriginalData({ ...newPersonality });
    setModal({ type: null });
    showAlert('success', `Created ${name}. Don't forget to save!`);
  };

  const handleCreateWithAI = async (name: string) => {
    setSaving(true);
    showAlert('info', `Generating personality for ${name}...`);

    try {
      const response = await fetch(`${config.API_URL}/api/generate_personality`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      });
      const data = await response.json();

      if (data.success) {
        setPersonalities(prev => ({ ...prev, [name]: data.personality }));
        setSelectedName(name);
        setFormData({ ...data.personality });
        setOriginalData({ ...data.personality });
        showAlert('success', `AI generated ${name}! Review and save.`);
      } else {
        showAlert('error', 'Generation failed: ' + (data.message || data.error));
        handleCreateManual(name);
      }
    } catch {
      showAlert('error', 'Error generating personality');
      handleCreateManual(name);
    } finally {
      setSaving(false);
      setModal({ type: null });
    }
  };

  const handleSaveAvatarDescription = async () => {
    if (!selectedName || !formData) return;

    try {
      const response = await fetch(`${config.API_URL}/api/personality/${selectedName}/avatar-description`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ avatar_description: formData.avatar_description || '' })
      });
      const data = await response.json();

      if (data.success) {
        showAlert('success', 'Avatar description saved');
      } else {
        showAlert('error', 'Failed to save description');
      }
    } catch {
      showAlert('error', 'Error saving description');
    }
  };

  const handleCancel = () => {
    if (originalData) {
      setFormData({ ...originalData });
    } else {
      setSelectedName(null);
      setFormData(null);
    }
  };

  const hasChanges = formData && originalData && JSON.stringify(formData) !== JSON.stringify(originalData);

  const characterNames = Object.keys(personalities).sort();

  // Safely merge API data with defaults to handle missing/partial data
  const defaultTraits = getDefaultTraits();
  const traits: PersonalityTraits = {
    ...defaultTraits,
    ...(formData?.personality_traits || {})
  };

  const defaultElasticity = getDefaultElasticity();
  const elasticityConfig: ElasticityConfig = {
    ...defaultElasticity,
    ...(formData?.elasticity_config || {}),
    trait_elasticity: {
      ...defaultElasticity.trait_elasticity,
      ...(formData?.elasticity_config?.trait_elasticity || {})
    }
  };

  return (
    <PageLayout variant="top" glowColor="gold" maxWidth="lg">
      <PageHeader
        title="Character Manager"
        subtitle="Create and customize AI opponents"
        onBack={onBack}
        titleVariant="primary"
      />

      {/* Alert Toast */}
      {alert && (
        <div className={`pm-alert pm-alert--${alert.type}`}>
          <span className="pm-alert__icon">
            {alert.type === 'success' && '✓'}
            {alert.type === 'error' && '✕'}
            {alert.type === 'info' && 'ℹ'}
          </span>
          <span className="pm-alert__message">{alert.message}</span>
          <button className="pm-alert__close" onClick={() => setAlert(null)}>×</button>
        </div>
      )}

      {/* Loading State */}
      {loading ? (
        <div className="pm-loading">
          <div className="pm-loading__spinner" />
          <span>Loading personalities...</span>
        </div>
      ) : (
        <div className="pm-container">
          {/* Character Selector Trigger */}
          <button
            type="button"
            className="pm-selector-trigger"
            onClick={() => setSelectorOpen(true)}
          >
            {selectedName ? (
              <>
                <span className="pm-selector-trigger__avatar">{selectedName.charAt(0)}</span>
                <span className="pm-selector-trigger__name">{selectedName}</span>
                <span className="pm-selector-trigger__change">Change</span>
              </>
            ) : (
              <>
                <span className="pm-selector-trigger__icon">
                  <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                    <circle cx="10" cy="6" r="4" stroke="currentColor" strokeWidth="1.5"/>
                    <path d="M3 18C3 14.134 6.134 11 10 11C13.866 11 17 14.134 17 18" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                  </svg>
                </span>
                <span className="pm-selector-trigger__placeholder">Select a character to edit</span>
              </>
            )}
            <svg className="pm-selector-trigger__chevron" width="20" height="20" viewBox="0 0 20 20" fill="none">
              <path d="M5 7.5L10 12.5L15 7.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>

          {/* Character Selector Bottom Sheet */}
          <CharacterSelector
            characters={characterNames}
            selected={selectedName}
            onSelect={selectPersonality}
            onCreate={() => { setSelectorOpen(false); setModal({ type: 'create' }); }}
            isOpen={selectorOpen}
            onClose={() => setSelectorOpen(false)}
          />

          {/* Editor */}
          {selectedName && formData ? (
            <div className="pm-editor">
              {/* Collapsible Sections */}
              <div className="pm-sections">
                {/* Basic Info */}
                <CollapsibleSection
                  title="Basic Info"
                  icon="📋"
                  isOpen={openSections.basic}
                  onToggle={() => toggleSection('basic')}
                >
                  <div className="pm-field">
                    <label className="pm-field__label" htmlFor="play_style">Play Style</label>
                    <input
                      id="play_style"
                      type="text"
                      className="pm-field__input"
                      value={formData.play_style || ''}
                      onChange={(e) => updateFormData({ play_style: e.target.value })}
                      placeholder="e.g., aggressive and boastful"
                    />
                  </div>
                  <div className="pm-field-row">
                    <div className="pm-field">
                      <label className="pm-field__label" htmlFor="confidence">Confidence</label>
                      <input
                        id="confidence"
                        type="text"
                        className="pm-field__input"
                        value={formData.default_confidence || ''}
                        onChange={(e) => updateFormData({ default_confidence: e.target.value })}
                        placeholder="e.g., supreme"
                      />
                    </div>
                    <div className="pm-field">
                      <label className="pm-field__label" htmlFor="attitude">Attitude</label>
                      <input
                        id="attitude"
                        type="text"
                        className="pm-field__input"
                        value={formData.default_attitude || ''}
                        onChange={(e) => updateFormData({ default_attitude: e.target.value })}
                        placeholder="e.g., domineering"
                      />
                    </div>
                  </div>
                </CollapsibleSection>

                {/* Personality Traits */}
                <CollapsibleSection
                  title="Personality Traits"
                  icon="🎭"
                  isOpen={openSections.traits}
                  onToggle={() => toggleSection('traits')}
                >
                  <TraitSlider
                    id="bluff_tendency"
                    label="Bluff Tendency"
                    value={traits.bluff_tendency}
                    elasticity={elasticityConfig.trait_elasticity.bluff_tendency}
                    onChange={(v) => updateTraits('bluff_tendency', v)}
                    onElasticityChange={(v) => updateElasticity('bluff_tendency', v)}
                  />
                  <TraitSlider
                    id="aggression"
                    label="Aggression"
                    value={traits.aggression}
                    elasticity={elasticityConfig.trait_elasticity.aggression}
                    onChange={(v) => updateTraits('aggression', v)}
                    onElasticityChange={(v) => updateElasticity('aggression', v)}
                  />
                  <TraitSlider
                    id="chattiness"
                    label="Chattiness"
                    value={traits.chattiness}
                    elasticity={elasticityConfig.trait_elasticity.chattiness}
                    onChange={(v) => updateTraits('chattiness', v)}
                    onElasticityChange={(v) => updateElasticity('chattiness', v)}
                  />
                  <TraitSlider
                    id="emoji_usage"
                    label="Emoji Usage"
                    value={traits.emoji_usage}
                    elasticity={elasticityConfig.trait_elasticity.emoji_usage}
                    onChange={(v) => updateTraits('emoji_usage', v)}
                    onElasticityChange={(v) => updateElasticity('emoji_usage', v)}
                  />
                </CollapsibleSection>

                {/* Elasticity Settings */}
                <CollapsibleSection
                  title="Mood & Recovery"
                  icon="🔄"
                  isOpen={openSections.elasticity}
                  onToggle={() => toggleSection('elasticity')}
                >
                  <TraitSlider
                    id="mood_elasticity"
                    label="Mood Elasticity"
                    value={elasticityConfig.mood_elasticity}
                    elasticity={0}
                    onChange={(v) => updateMoodSettings('mood_elasticity', v)}
                    onElasticityChange={() => {}}
                    showElasticity={false}
                  />
                  <p className="pm-help-text">How reactive mood changes are to game events</p>
                  <TraitSlider
                    id="recovery_rate"
                    label="Recovery Rate"
                    value={elasticityConfig.recovery_rate}
                    elasticity={0}
                    onChange={(v) => updateMoodSettings('recovery_rate', v)}
                    onElasticityChange={() => {}}
                    showElasticity={false}
                  />
                  <p className="pm-help-text">How quickly traits return to baseline</p>
                </CollapsibleSection>

                {/* Verbal & Physical Tics */}
                <CollapsibleSection
                  title="Quirks & Tics"
                  icon="💬"
                  isOpen={openSections.tics}
                  onToggle={() => toggleSection('tics')}
                  badge={`${(formData.verbal_tics?.length || 0) + (formData.physical_tics?.length || 0)}`}
                >
                  <ArrayInput
                    label="Verbal Tics"
                    items={formData.verbal_tics || []}
                    onChange={(items) => updateFormData({ verbal_tics: items })}
                    placeholder="e.g., Says 'you know' frequently"
                  />
                  <ArrayInput
                    label="Physical Tics"
                    items={formData.physical_tics || []}
                    onChange={(items) => updateFormData({ physical_tics: items })}
                    placeholder="e.g., Taps chips when nervous"
                  />
                </CollapsibleSection>

                {/* Avatar Images */}
                <CollapsibleSection
                  title="Avatar Images"
                  icon="🖼️"
                  isOpen={openSections.avatar}
                  onToggle={() => toggleSection('avatar')}
                >
                  <AvatarImageManager
                    personalityName={selectedName}
                    avatarDescription={formData.avatar_description || ''}
                    onDescriptionChange={(desc) => updateFormData({ avatar_description: desc })}
                    onDescriptionSave={handleSaveAvatarDescription}
                  />
                </CollapsibleSection>
              </div>

              {/* Sticky Action Bar */}
              <div className="pm-actions">
                <div className="pm-actions__secondary">
                  <button
                    type="button"
                    className="pm-actions__btn pm-actions__btn--ghost"
                    onClick={() => setModal({ type: 'regenerate' })}
                    disabled={saving}
                  >
                    ✨ AI Regen
                  </button>
                  <button
                    type="button"
                    className="pm-actions__btn pm-actions__btn--danger"
                    onClick={() => setModal({ type: 'delete' })}
                    disabled={saving}
                  >
                    Delete
                  </button>
                </div>
                <div className="pm-actions__primary">
                  {hasChanges && (
                    <button
                      type="button"
                      className="pm-actions__btn pm-actions__btn--ghost"
                      onClick={handleCancel}
                      disabled={saving}
                    >
                      Cancel
                    </button>
                  )}
                  <button
                    type="button"
                    className="pm-actions__btn pm-actions__btn--save"
                    onClick={handleSave}
                    disabled={saving || !hasChanges}
                  >
                    {saving ? 'Saving...' : 'Save Changes'}
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div className="pm-empty">
              <div className="pm-empty__icon">🎭</div>
              <h3 className="pm-empty__title">No Character Selected</h3>
              <p className="pm-empty__text">Choose a character above or create a new one</p>
              <button
                type="button"
                className="pm-empty__create"
                onClick={() => setModal({ type: 'create' })}
              >
                Create New Character
              </button>
            </div>
          )}

          {/* Floating Create Button */}
          <button
            type="button"
            className="pm-fab"
            onClick={() => setModal({ type: 'create' })}
            aria-label="Create new character"
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
              <path d="M12 5V19M5 12H19" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"/>
            </svg>
          </button>
        </div>
      )}

      {/* Modals */}
      {modal.type === 'delete' && (
        <ConfirmModal
          title="Delete Character"
          message={`Are you sure you want to delete "${selectedName}"? This action cannot be undone.`}
          confirmLabel="Delete"
          confirmVariant="danger"
          onConfirm={handleDelete}
          onCancel={() => setModal({ type: null })}
          isLoading={saving}
        />
      )}

      {modal.type === 'regenerate' && (
        <ConfirmModal
          title="Regenerate with AI"
          message={`This will replace "${selectedName}" with a new AI-generated personality. Your current changes will be lost.`}
          confirmLabel="Regenerate"
          confirmVariant="warning"
          onConfirm={handleRegenerate}
          onCancel={() => setModal({ type: null })}
          isLoading={saving}
        />
      )}

      {modal.type === 'create' && (
        <CreateModal
          existingNames={characterNames}
          onCreateManual={handleCreateManual}
          onCreateWithAI={handleCreateWithAI}
          onCancel={() => setModal({ type: null })}
          isLoading={saving}
        />
      )}
    </PageLayout>
  );
}

// ============================================
// Helpers
// ============================================

function getDefaultTraits(): PersonalityTraits {
  return {
    bluff_tendency: 0.5,
    aggression: 0.5,
    chattiness: 0.5,
    emoji_usage: 0.3
  };
}

function getDefaultElasticity(): ElasticityConfig {
  return {
    trait_elasticity: {
      bluff_tendency: 0.3,
      aggression: 0.3,
      chattiness: 0.5,
      emoji_usage: 0.3
    },
    mood_elasticity: 0.4,
    recovery_rate: 0.1
  };
}
