import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { User, Brain, MessageCircle, Image as ImageIcon, Coins } from 'lucide-react';
import { config } from '../../config';
import { adminFetch } from '../../utils/api';
import { PageLayout, PageHeader } from '../shared';
import { useViewport } from '../../hooks/useViewport';
import { logger } from '../../utils/logger';
import './AdminShared.css';
import './PersonalityManager.css';

// ============================================
// Types
// ============================================

interface PersonalityAnchors {
  baseline_aggression: number;
  baseline_looseness: number;
  ego: number;
  poise: number;
  expressiveness: number;
  risk_identity: number;
  adaptation_bias: number;
  baseline_energy: number;
  recovery_rate: number;
}

interface PersonalityData {
  play_style?: string;
  default_confidence?: string;
  default_attitude?: string;
  anchors?: PersonalityAnchors;
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
  icon: React.ReactNode;
  isOpen: boolean;
  onToggle: () => void;
  children: React.ReactNode;
  badge?: string;
}

function CollapsibleSection({
  title,
  icon,
  isOpen,
  onToggle,
  children,
  badge,
}: CollapsibleSectionProps) {
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
            <path
              d="M5 7.5L10 12.5L15 7.5"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
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

function TraitSlider({
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

interface ConfirmModalProps {
  title: string;
  message: string;
  confirmLabel: string;
  confirmVariant?: 'danger' | 'warning' | 'primary';
  onConfirm: () => void;
  onCancel: () => void;
  isLoading?: boolean;
}

function ConfirmModal({
  title,
  message,
  confirmLabel,
  confirmVariant = 'primary',
  onConfirm,
  onCancel,
  isLoading,
}: ConfirmModalProps) {
  return (
    <div className="admin-modal-overlay" onClick={onCancel}>
      <div className="admin-modal" onClick={(e) => e.stopPropagation()}>
        <div className="admin-modal__header">
          <h3 className="admin-modal__title">{title}</h3>
        </div>
        <div className="admin-modal__body">
          <p style={{ margin: 0, color: 'var(--color-text-secondary)' }}>{message}</p>
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
          <button
            type="button"
            className={`admin-btn admin-btn--${confirmVariant}`}
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

// ============================================
// Image Lightbox
// ============================================

interface ImageLightboxProps {
  images: EmotionImage[];
  personalityName: string;
  initialIndex: number;
  referenceImageId: string | null;
  onClose: () => void;
  onRegenerate: (
    emotion: string,
    referenceImageId?: string | null,
    strength?: number
  ) => Promise<void>;
  onRegenerateAll: (referenceImageId?: string | null, strength?: number) => Promise<void>;
  onReferenceImageChange: (referenceId: string | null) => void;
  regenerating: string | null;
}

function ImageLightbox({
  images,
  personalityName,
  initialIndex,
  referenceImageId,
  onClose,
  onRegenerate,
  onRegenerateAll,
  onReferenceImageChange,
  regenerating,
}: ImageLightboxProps) {
  const [currentIndex, setCurrentIndex] = useState(initialIndex);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [urlInput, setUrlInput] = useState('');
  const [showUrlInput, setShowUrlInput] = useState(false);
  const [imagePreview, setImagePreview] = useState<string | null>(null);
  // Strength: 0.0 = keep original exactly, 1.0 = fully transform
  // UI shows (1 - strength) as %, so 0.8 = 20% displayed
  // Range limited to 0.5-1.0 (50%-0% displayed)
  const [strength, setStrength] = useState(0.8);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const currentImage = images[currentIndex];
  const emotionCount = images.length;

  // Load reference image preview when referenceImageId changes
  useEffect(() => {
    if (referenceImageId) {
      setImagePreview(`${config.API_URL}/admin/api/reference-images/${referenceImageId}`);
    } else {
      setImagePreview(null);
    }
  }, [referenceImageId]);

  // Keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      } else if (e.key === 'ArrowLeft') {
        setCurrentIndex((prev) => (prev - 1 + emotionCount) % emotionCount);
      } else if (e.key === 'ArrowRight') {
        setCurrentIndex((prev) => (prev + 1) % emotionCount);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [emotionCount, onClose]);

  const handlePrev = () => {
    setCurrentIndex((prev) => (prev - 1 + emotionCount) % emotionCount);
  };

  const handleNext = () => {
    setCurrentIndex((prev) => (prev + 1) % emotionCount);
  };

  // Handle file upload
  const handleFileUpload = async (file: File) => {
    setIsUploading(true);
    setUploadError(null);

    try {
      const formData = new FormData();
      formData.append('file', file);

      const response = await adminFetch('/admin/api/reference-images', {
        method: 'POST',
        body: formData,
      });

      const data = await response.json();

      if (data.success && data.reference_id) {
        onReferenceImageChange(data.reference_id);
      } else {
        setUploadError(data.error || 'Upload failed');
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setIsUploading(false);
    }
  };

  // Handle URL submission
  const handleUrlSubmit = async () => {
    if (!urlInput.trim()) return;

    setIsUploading(true);
    setUploadError(null);

    try {
      const response = await adminFetch('/admin/api/reference-images', {
        method: 'POST',
        body: JSON.stringify({ url: urlInput.trim() }),
      });

      const data = await response.json();

      if (data.success && data.reference_id) {
        onReferenceImageChange(data.reference_id);
        setUrlInput('');
        setShowUrlInput(false);
      } else {
        setUploadError(data.error || 'Failed to fetch URL');
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Failed to fetch URL');
    } finally {
      setIsUploading(false);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      handleFileUpload(file);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();

    const file = e.dataTransfer.files?.[0];
    if (file && file.type.startsWith('image/')) {
      handleFileUpload(file);
    }
  };

  const handleClearReference = () => {
    onReferenceImageChange(null);
    setUrlInput('');
    setShowUrlInput(false);
    setUploadError(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleRegenerateThis = () => {
    onRegenerate(currentImage.emotion, referenceImageId, referenceImageId ? strength : undefined);
  };

  const handleApplyToAll = () => {
    onRegenerateAll(referenceImageId, referenceImageId ? strength : undefined);
  };

  const isRegenerating = regenerating !== null;

  return (
    <div className="pm-lightbox-overlay" onClick={onClose}>
      <div className="pm-lightbox" onClick={(e) => e.stopPropagation()}>
        {/* Close button */}
        <button type="button" className="pm-lightbox__close" onClick={onClose}>
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
            <path
              d="M6 6L18 18M18 6L6 18"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </svg>
        </button>

        {/* Navigation arrows */}
        <button
          type="button"
          className="pm-lightbox__nav pm-lightbox__nav--prev"
          onClick={handlePrev}
        >
          <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
            <path
              d="M20 8L12 16L20 24"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <button
          type="button"
          className="pm-lightbox__nav pm-lightbox__nav--next"
          onClick={handleNext}
        >
          <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
            <path
              d="M12 8L20 16L12 24"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>

        {/* Main image area */}
        <div className="pm-lightbox__image-container">
          {currentImage.url ? (
            <img
              src={currentImage.url}
              alt={`${personalityName} - ${currentImage.emotion}`}
              className="pm-lightbox__image"
            />
          ) : (
            <div className="pm-lightbox__placeholder">
              <span>No image</span>
            </div>
          )}
          {(regenerating === currentImage.emotion || regenerating === 'all') && (
            <div className="pm-lightbox__regenerating">
              <div className="admin-loading__spinner" />
              <span>Regenerating...</span>
            </div>
          )}
        </div>

        {/* Info bar */}
        <div className="pm-lightbox__info">
          <span className="pm-lightbox__name">{personalityName}</span>
          <span className="pm-lightbox__emotion">{currentImage.emotion}</span>
          <span className="pm-lightbox__pagination">
            {currentIndex + 1} / {emotionCount}
          </span>
        </div>

        {/* Controls panel */}
        <div className="pm-lightbox__controls">
          {/* Reference image section */}
          <div className="pm-lightbox__reference">
            <div className="pm-lightbox__reference-header">
              <span className="pm-lightbox__reference-label">Reference Image (optional)</span>
              {imagePreview && (
                <button
                  type="button"
                  className="pm-lightbox__reference-clear"
                  onClick={handleClearReference}
                  disabled={isUploading || isRegenerating}
                >
                  Clear
                </button>
              )}
            </div>

            {imagePreview ? (
              <div className="pm-lightbox__reference-preview">
                <img src={imagePreview} alt="Reference" />
              </div>
            ) : (
              <div
                className={`pm-lightbox__reference-upload ${isUploading ? 'uploading' : ''}`}
                onDragOver={handleDragOver}
                onDrop={!isUploading && !isRegenerating ? handleDrop : undefined}
                onClick={() =>
                  !isUploading && !isRegenerating && !showUrlInput && fileInputRef.current?.click()
                }
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  onChange={handleFileChange}
                  disabled={isUploading || isRegenerating}
                  style={{ display: 'none' }}
                />

                {isUploading ? (
                  <div className="pm-lightbox__upload-status">
                    <div className="admin-loading__spinner admin-loading__spinner--sm" />
                    <span>Uploading...</span>
                  </div>
                ) : showUrlInput ? (
                  <div className="pm-lightbox__url-input" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="url"
                      placeholder="https://example.com/image.jpg"
                      value={urlInput}
                      onChange={(e) => setUrlInput(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && handleUrlSubmit()}
                      disabled={isRegenerating}
                      autoFocus
                    />
                    <div className="pm-lightbox__url-actions">
                      <button
                        type="button"
                        onClick={handleUrlSubmit}
                        disabled={!urlInput.trim() || isRegenerating}
                      >
                        Load
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setShowUrlInput(false);
                          setUrlInput('');
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="pm-lightbox__upload-prompt">
                    <span className="pm-lightbox__upload-icon">📷</span>
                    <span className="pm-lightbox__upload-text">Drop image or click to upload</span>
                    <button
                      type="button"
                      className="pm-lightbox__url-link"
                      onClick={(e) => {
                        e.stopPropagation();
                        setShowUrlInput(true);
                      }}
                      disabled={isRegenerating}
                    >
                      or paste URL
                    </button>
                  </div>
                )}
              </div>
            )}

            {uploadError && <div className="pm-lightbox__error">{uploadError}</div>}

            {/* Strength slider - only show when reference image is set */}
            {imagePreview && (
              <div className="pm-lightbox__strength">
                <label className="pm-lightbox__strength-label">
                  <span>Reference Strength</span>
                  <span className="pm-lightbox__strength-value">
                    {Math.round((1 - strength) * 100)}%
                  </span>
                </label>
                <input
                  type="range"
                  min="0.5"
                  max="1"
                  step="0.05"
                  value={strength}
                  onChange={(e) => setStrength(parseFloat(e.target.value))}
                  className="pm-lightbox__strength-slider"
                  disabled={isRegenerating}
                />
                <div className="pm-lightbox__strength-hints">
                  <span>More like reference</span>
                  <span>More creative</span>
                </div>
              </div>
            )}
          </div>

          {/* Action buttons */}
          <div className="pm-lightbox__actions">
            <button
              type="button"
              className="admin-btn admin-btn--primary"
              onClick={handleRegenerateThis}
              disabled={isRegenerating}
            >
              {regenerating === currentImage.emotion ? 'Regenerating...' : 'Regenerate This'}
            </button>
            <button
              type="button"
              className="admin-btn admin-btn--secondary"
              onClick={handleApplyToAll}
              disabled={isRegenerating}
            >
              {regenerating === 'all' ? 'Regenerating All...' : 'Apply to All'}
            </button>
          </div>
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

function CreateModal({
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

// Shared icons
const SearchIcon = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
    <circle cx="8" cy="8" r="5.5" stroke="currentColor" strokeWidth="1.5" />
    <path d="M12 12L16 16" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
  </svg>
);

const CheckIcon = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
    <path
      d="M4 9L7.5 12.5L14 5.5"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

const PlusIcon = ({ size = 18 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 18 18" fill="none">
    <path d="M9 3V15M3 9H15" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
  </svg>
);

const MenuIcon = () => (
  <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
    <path
      d="M3 5H17M3 10H17M3 15H17"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
    />
  </svg>
);

// Reusable MasterList component for desktop sidebar
interface MasterListProps {
  characters: string[];
  groups: { label: string; names: string[] }[];
  selected: string | null;
  onSelect: (name: string) => void;
  onCreate: () => void;
  search: string;
  onSearchChange: (search: string) => void;
  personalityMeta?: Record<string, { visibility?: string; owner_id?: string }>;
}

function MasterList({
  characters,
  groups,
  selected,
  onSelect,
  onCreate,
  search,
  onSearchChange,
  personalityMeta,
}: MasterListProps) {
  const searchLower = search.toLowerCase();

  const filteredGroups = useMemo(
    () =>
      groups
        .map((g) => ({
          ...g,
          names: g.names.filter((name) => name.toLowerCase().includes(searchLower)),
        }))
        .filter((g) => g.names.length > 0),
    [groups, searchLower]
  );

  const totalFiltered = filteredGroups.reduce((sum, g) => sum + g.names.length, 0);

  return (
    <>
      <div className="admin-master__header">
        <h3 className="admin-master__title">Characters</h3>
        <span className="admin-master__count">{characters.length}</span>
      </div>
      <div className="admin-master__search">
        <div className="admin-master__search-wrap">
          <span className="admin-master__search-icon">
            <SearchIcon />
          </span>
          <input
            type="text"
            className="admin-master__search-input"
            placeholder="Search..."
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
          />
        </div>
      </div>
      <div className="admin-master__list">
        {filteredGroups.map((group) => (
          <div key={group.label}>
            {groups.length > 1 && <div className="admin-master__section-header">{group.label}</div>}
            {group.names.map((name) => {
              const vis = personalityMeta?.[name]?.visibility;
              return (
                <button
                  key={name}
                  type="button"
                  className={`admin-master__item ${selected === name ? 'admin-master__item--selected' : ''}`}
                  onClick={() => onSelect(name)}
                >
                  <span className="admin-master__item-avatar">{name.charAt(0)}</span>
                  <span className="admin-master__item-name">{name}</span>
                  {vis && vis !== 'public' && (
                    <span className={`pm-visibility-badge pm-visibility-badge--${vis}`} title={vis}>
                      {vis === 'private' ? '🔒' : '⊘'}
                    </span>
                  )}
                  {selected === name && (
                    <span className="admin-master__item-check">
                      <CheckIcon />
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
        {totalFiltered === 0 && (
          <div className="admin-master__empty">
            No characters found{search ? ` matching "${search}"` : ''}
          </div>
        )}
      </div>
      <div className="admin-master__footer">
        <button type="button" className="admin-master__create" onClick={onCreate}>
          <PlusIcon />
          New Character
        </button>
      </div>
    </>
  );
}

interface CharacterSelectorProps {
  characters: string[];
  groups: { label: string; names: string[] }[];
  selected: string | null;
  onSelect: (name: string) => void;
  onCreate: () => void;
  isOpen: boolean;
  onClose: () => void;
  personalityMeta?: Record<string, { visibility?: string; owner_id?: string }>;
}

function CharacterSelector({
  characters,
  groups,
  selected,
  onSelect,
  onCreate,
  isOpen,
  onClose,
  personalityMeta,
}: CharacterSelectorProps) {
  const [search, setSearch] = useState('');
  const searchLower = search.toLowerCase();

  const filteredGroups = groups
    .map((g) => ({
      ...g,
      names: g.names.filter((name) => name.toLowerCase().includes(searchLower)),
    }))
    .filter((g) => g.names.length > 0);

  const totalFiltered = filteredGroups.reduce((sum, g) => sum + g.names.length, 0);
  let itemIndex = 0;

  const handleSelect = (name: string) => {
    onSelect(name);
    onClose();
  };

  return (
    <>
      <div
        className={`pm-sheet-backdrop ${isOpen ? 'pm-sheet-backdrop--visible' : ''}`}
        onClick={onClose}
      />
      <div className={`pm-sheet ${isOpen ? 'pm-sheet--open' : ''}`}>
        <div className="pm-sheet__handle" onClick={onClose}>
          <div className="pm-sheet__handle-bar" />
        </div>
        <div className="pm-sheet__header">
          <h3 className="pm-sheet__title">Select Character</h3>
          <span className="pm-sheet__count">{characters.length} personalities</span>
        </div>
        <div className="pm-sheet__search">
          <span className="pm-sheet__search-icon">
            <SearchIcon />
          </span>
          <input
            type="text"
            className="pm-sheet__search-input"
            placeholder="Search characters..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="pm-sheet__list">
          {filteredGroups.map((group) => (
            <div key={group.label}>
              {groups.length > 1 && (
                <div className="admin-master__section-header">{group.label}</div>
              )}
              {group.names.map((name) => {
                const vis = personalityMeta?.[name]?.visibility;
                const idx = itemIndex++;
                return (
                  <button
                    key={name}
                    type="button"
                    className={`pm-sheet__item ${selected === name ? 'pm-sheet__item--active' : ''}`}
                    onClick={() => handleSelect(name)}
                    style={{ animationDelay: `${idx * 20}ms` }}
                  >
                    <span className="pm-sheet__item-avatar">{name.charAt(0)}</span>
                    <span className="pm-sheet__item-name">{name}</span>
                    {vis && vis !== 'public' && (
                      <span className={`pm-visibility-badge pm-visibility-badge--${vis}`}>
                        {vis === 'private' ? '🔒' : '⊘'}
                      </span>
                    )}
                    {selected === name && (
                      <span className="pm-sheet__item-check">
                        <CheckIcon />
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          ))}
          {totalFiltered === 0 && (
            <div className="pm-sheet__empty">No characters found matching &quot;{search}&quot;</div>
          )}
        </div>
        <button type="button" className="pm-sheet__create" onClick={onCreate}>
          <PlusIcon />
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

function AvatarImageManager({
  personalityName,
  avatarDescription,
  onDescriptionChange,
  onDescriptionSave,
}: AvatarImageManagerProps) {
  const [images, setImages] = useState<EmotionImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [regenerating, setRegenerating] = useState<string | null>(null);
  const [savingDescription, setSavingDescription] = useState(false);

  // Lightbox state
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [referenceImageId, setReferenceImageId] = useState<string | null>(null);

  const getEmotionImageUrl = (emotion: string) =>
    `${config.API_URL}/api/avatar/${encodeURIComponent(personalityName)}/${emotion}/full?t=${Date.now()}`;

  const markEmotionReady = (emotion: string) => {
    setImages((prev) =>
      prev.map((img) =>
        img.emotion === emotion
          ? { ...img, url: getEmotionImageUrl(emotion), hasFullImage: true }
          : img
      )
    );
  };

  useEffect(() => {
    loadEmotionsAndImages();
    loadReferenceImage();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [personalityName]);

  const loadEmotionsAndImages = async () => {
    setLoading(true);
    try {
      // Fetch available emotions
      const emotionsRes = await fetch(`${config.API_URL}/api/avatar/emotions`);
      const emotionsData = await emotionsRes.json();
      const emotionsList = emotionsData.emotions || [
        'confident',
        'happy',
        'thinking',
        'nervous',
        'angry',
        'shocked',
        'smug',
        'frustrated',
        'elated',
        'poker_face',
      ];

      // Check which images exist - use full/square images for the manager
      const imagePromises = emotionsList.map(async (emotion: string) => {
        try {
          const fullRes = await fetch(
            `${config.API_URL}/api/avatar/${encodeURIComponent(personalityName)}/${emotion}/full`,
            { method: 'HEAD' }
          );
          return {
            emotion,
            // Use the full/square image if available
            url: fullRes.ok
              ? `${config.API_URL}/api/avatar/${encodeURIComponent(personalityName)}/${emotion}/full`
              : null,
            hasFullImage: fullRes.ok,
          };
        } catch {
          return { emotion, url: null, hasFullImage: false };
        }
      });

      const imageResults = await Promise.all(imagePromises);
      setImages(imageResults);
    } catch (error) {
      logger.error('Failed to load avatar images:', error);
    } finally {
      setLoading(false);
    }
  };

  const loadReferenceImage = async () => {
    try {
      const res = await fetch(
        `${config.API_URL}/api/personality/${encodeURIComponent(personalityName)}/reference-image`
      );
      const data = await res.json();
      if (data.success) {
        setReferenceImageId(data.reference_image_id || null);
      }
    } catch (error) {
      logger.error('Failed to load reference image:', error);
    }
  };

  const handleReferenceImageChange = async (newReferenceId: string | null) => {
    setReferenceImageId(newReferenceId);
    // Save to backend
    try {
      await fetch(
        `${config.API_URL}/api/personality/${encodeURIComponent(personalityName)}/reference-image`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reference_image_id: newReferenceId }),
        }
      );
    } catch (error) {
      logger.error('Failed to save reference image:', error);
    }
  };

  const handleRegenerate = async (
    emotion: string,
    refImageId?: string | null,
    strengthValue?: number
  ) => {
    setRegenerating(emotion);
    try {
      const body: { emotions: string[]; reference_image_id?: string; strength?: number } = {
        emotions: [emotion],
      };
      if (refImageId) {
        body.reference_image_id = refImageId;
        if (strengthValue !== undefined) {
          body.strength = strengthValue;
        }
      }

      const res = await fetch(
        `${config.API_URL}/api/avatar/${encodeURIComponent(personalityName)}/regenerate`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }
      );
      const data = await res.json();
      if (data.success) {
        // Update avatar description if it was auto-generated
        if (data.avatar_description && data.avatar_description !== avatarDescription) {
          onDescriptionChange(data.avatar_description);
        }
        markEmotionReady(emotion);
      }
    } catch (error) {
      logger.error('Failed to regenerate:', error);
    } finally {
      setRegenerating(null);
    }
  };

  const handleRegenerateAll = async (refImageId?: string | null, strengthValue?: number) => {
    const allEmotions = images.map((img) => img.emotion);
    setRegenerating('all');
    try {
      for (const emotion of allEmotions) {
        const body: { emotions: string[]; reference_image_id?: string; strength?: number } = {
          emotions: [emotion],
        };
        if (refImageId) {
          body.reference_image_id = refImageId;
          if (strengthValue !== undefined) {
            body.strength = strengthValue;
          }
        }

        try {
          const res = await fetch(
            `${config.API_URL}/api/avatar/${encodeURIComponent(personalityName)}/regenerate`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(body),
            }
          );
          const data = await res.json();
          if (data.success) {
            // Update avatar description if it was auto-generated
            if (data.avatar_description && data.avatar_description !== avatarDescription) {
              onDescriptionChange(data.avatar_description);
            }
            markEmotionReady(emotion);
          }
        } catch (error) {
          console.error(`Failed to regenerate ${emotion}:`, error);
        }
      }
    } catch (error) {
      logger.error('Failed to regenerate all:', error);
    } finally {
      setRegenerating(null);
    }
  };

  const handleGenerateMissing = async () => {
    const missing = images.filter((img) => !img.url).map((img) => img.emotion);
    if (missing.length === 0) return;

    setRegenerating('all');
    try {
      for (const emotion of missing) {
        try {
          const res = await fetch(
            `${config.API_URL}/api/avatar/${encodeURIComponent(personalityName)}/regenerate`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ emotions: [emotion] }),
            }
          );
          const data = await res.json();
          if (data.success) {
            // Update avatar description if it was auto-generated
            if (data.avatar_description && data.avatar_description !== avatarDescription) {
              onDescriptionChange(data.avatar_description);
            }
            markEmotionReady(emotion);
          }
        } catch (error) {
          console.error(`Failed to generate ${emotion}:`, error);
        }
      }
    } catch (error) {
      logger.error('Failed to generate missing:', error);
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

  const handleImageClick = (index: number) => {
    setLightboxIndex(index);
    setLightboxOpen(true);
  };

  const missingCount = images.filter((img) => !img.url).length;

  if (loading) {
    return (
      <div className="admin-loading">
        <div className="admin-loading__spinner" />
        <span className="admin-loading__text">Loading images...</span>
      </div>
    );
  }

  return (
    <div className="pm-avatar">
      <div className="pm-avatar__description">
        <label
          className="admin-label"
          htmlFor="avatar-desc"
          style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}
        >
          Image Description
          <span className="admin-help-text" style={{ margin: 0 }}>
            Used for AI image generation
          </span>
        </label>
        <textarea
          id="avatar-desc"
          className="admin-input admin-textarea"
          value={avatarDescription}
          onChange={(e) => onDescriptionChange(e.target.value)}
          placeholder="Describe this character's appearance for image generation..."
          rows={3}
        />
        <button
          type="button"
          className="admin-btn admin-btn--secondary"
          onClick={handleSaveDescription}
          disabled={savingDescription}
          style={{ marginTop: 'var(--space-2)' }}
        >
          {savingDescription ? 'Saving...' : 'Save Description'}
        </button>
      </div>

      <div className="pm-avatar__grid">
        {images.map(({ emotion, url }, index) => (
          <div key={emotion} className="pm-avatar__card">
            <div
              className="pm-avatar__image-wrap pm-avatar__image-wrap--clickable"
              onClick={() => handleImageClick(index)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === 'Enter' && handleImageClick(index)}
            >
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
                  <div className="admin-loading__spinner admin-loading__spinner--sm" />
                </div>
              )}
            </div>
            <div className="pm-avatar__card-footer">
              <span className="pm-avatar__emotion">{emotion}</span>
              <button
                type="button"
                className="pm-avatar__refresh"
                onClick={(e) => {
                  e.stopPropagation();
                  handleRegenerate(emotion);
                }}
                disabled={regenerating !== null}
                title={`Regenerate ${emotion}`}
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <path
                    d="M1 7C1 3.686 3.686 1 7 1C9.21 1 11.117 2.214 12.143 4M13 7C13 10.314 10.314 13 7 13C4.79 13 2.883 11.786 1.857 10M12.143 4V1M12.143 4H9.143M1.857 10V13M1.857 10H4.857"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
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
              <div className="admin-loading__spinner admin-loading__spinner--sm" />
              Generating...
            </>
          ) : (
            <>
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path
                  d="M8 1V15M1 8H15"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
              </svg>
              Generate {missingCount} Missing Image{missingCount > 1 ? 's' : ''}
            </>
          )}
        </button>
      )}

      {/* Lightbox modal */}
      {lightboxOpen && (
        <ImageLightbox
          images={images}
          personalityName={personalityName}
          initialIndex={lightboxIndex}
          referenceImageId={referenceImageId}
          onClose={() => setLightboxOpen(false)}
          onRegenerate={handleRegenerate}
          onRegenerateAll={handleRegenerateAll}
          onReferenceImageChange={handleReferenceImageChange}
          regenerating={regenerating}
        />
      )}
    </div>
  );
}

// ============================================
// BankrollKnobsSection (cash mode admin)
// ============================================

interface BankrollKnobs {
  bankroll_cap: number;
  bankroll_rate: number;
  buy_in_multiplier: number;
  stake_comfort_zone: string;
}

interface BankrollKnobsResponse {
  success?: boolean;
  knobs?: BankrollKnobs;
  defaults?: BankrollKnobs;
  current_bankroll?: number | null;
  error?: string;
}

interface BankrollKnobsSectionProps {
  personalityName: string;
  showAlert: (type: AlertState['type'], message: string) => void;
}

const STAKE_COMFORT_OPTIONS = ['$2', '$10', '$50', '$200', '$1000'] as const;

function BankrollKnobsSection({ personalityName, showAlert }: BankrollKnobsSectionProps) {
  const [knobs, setKnobs] = useState<BankrollKnobs | null>(null);
  const [defaults, setDefaults] = useState<BankrollKnobs | null>(null);
  const [currentBankroll, setCurrentBankroll] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  // Track the last loaded snapshot so we can compute dirtiness — the
  // section saves independently of the main editor's Save button so
  // admins can iterate on knobs without re-saving the whole personality.
  const [original, setOriginal] = useState<BankrollKnobs | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await adminFetch(
        `/api/personality/${encodeURIComponent(personalityName)}/bankroll-knobs`
      );
      const data: BankrollKnobsResponse = await response.json();
      if (data.success && data.knobs && data.defaults) {
        setKnobs(data.knobs);
        setOriginal(data.knobs);
        setDefaults(data.defaults);
        setCurrentBankroll(data.current_bankroll ?? null);
      } else {
        showAlert('error', data.error || 'Failed to load bankroll knobs');
      }
    } catch (e) {
      logger.error('Failed to load bankroll knobs', e);
      showAlert('error', 'Error loading bankroll knobs');
    } finally {
      setLoading(false);
    }
  }, [personalityName, showAlert]);

  useEffect(() => {
    load();
  }, [load]);

  const updateField = <K extends keyof BankrollKnobs>(field: K, value: BankrollKnobs[K]) => {
    setKnobs((prev) => (prev ? { ...prev, [field]: value } : prev));
  };

  const hasChanges =
    !!knobs &&
    !!original &&
    (knobs.bankroll_cap !== original.bankroll_cap ||
      knobs.bankroll_rate !== original.bankroll_rate ||
      knobs.buy_in_multiplier !== original.buy_in_multiplier ||
      knobs.stake_comfort_zone !== original.stake_comfort_zone);

  const handleSave = async () => {
    if (!knobs) return;
    setSaving(true);
    try {
      const response = await adminFetch(
        `/api/personality/${encodeURIComponent(personalityName)}/bankroll-knobs`,
        {
          method: 'PUT',
          body: JSON.stringify(knobs),
        }
      );
      const data: BankrollKnobsResponse = await response.json();
      if (data.success && data.knobs) {
        setKnobs(data.knobs);
        setOriginal(data.knobs);
        showAlert('success', 'Bankroll knobs saved');
      } else {
        showAlert('error', data.error || 'Failed to save bankroll knobs');
      }
    } catch (e) {
      logger.error('Failed to save bankroll knobs', e);
      showAlert('error', 'Error saving bankroll knobs');
    } finally {
      setSaving(false);
    }
  };

  const handleResetToCap = async () => {
    if (!knobs) return;
    // "Reset bankroll to cap" is a testing convenience: it's a write
    // through the same admin route the live bankroll surface uses on
    // the read side, but there's no dedicated endpoint for it in v1.
    // We emulate it by re-saving the existing knobs (which leaves them
    // unchanged) and then telling the admin to seed via a future
    // endpoint. Until that lands, the affordance just refreshes the
    // current_bankroll display so the admin can see the live state.
    await load();
  };

  if (loading) {
    return <p className="admin-help-text">Loading bankroll knobs…</p>;
  }
  if (!knobs || !defaults) {
    return <p className="admin-help-text">No knobs data available.</p>;
  }

  return (
    <div className="pm-bankroll-knobs">
      <p className="admin-help-text" style={{ marginTop: 0 }}>
        Cash-mode bankroll behavior. The cap is a hard ceiling — table winnings above it evaporate
        when the AI cashes out.
      </p>

      <div className="admin-form-group">
        <label className="admin-label">Current live bankroll</label>
        <p className="admin-help-text" style={{ marginTop: 0 }}>
          {currentBankroll !== null
            ? `${currentBankroll.toLocaleString()} chips`
            : 'No bankroll row yet — AI has never sat at a cash table.'}{' '}
          <button
            type="button"
            className="admin-btn admin-btn--secondary"
            style={{ marginLeft: 'var(--space-2)' }}
            onClick={handleResetToCap}
          >
            Refresh
          </button>
        </p>
      </div>

      <div className="admin-form-row">
        <div className="admin-form-group">
          <label className="admin-label" htmlFor="bankroll_cap">
            Bankroll cap
          </label>
          <input
            id="bankroll_cap"
            type="number"
            className="admin-input"
            min={0}
            value={knobs.bankroll_cap}
            onChange={(e) => updateField('bankroll_cap', Number(e.target.value))}
          />
          <p className="admin-help-text">
            Hard ceiling; default {defaults.bankroll_cap.toLocaleString()}
          </p>
        </div>
        <div className="admin-form-group">
          <label className="admin-label" htmlFor="bankroll_rate">
            Bankroll rate
          </label>
          <input
            id="bankroll_rate"
            type="number"
            className="admin-input"
            min={0}
            value={knobs.bankroll_rate}
            onChange={(e) => updateField('bankroll_rate', Number(e.target.value))}
          />
          <p className="admin-help-text">
            Chips/day passive regen; default {defaults.bankroll_rate}
          </p>
        </div>
      </div>

      <div className="admin-form-row">
        <div className="admin-form-group">
          <label className="admin-label" htmlFor="buy_in_multiplier">
            Buy-in multiplier
          </label>
          <input
            id="buy_in_multiplier"
            type="number"
            className="admin-input"
            step="0.1"
            min={0.1}
            value={knobs.buy_in_multiplier}
            onChange={(e) => updateField('buy_in_multiplier', Number(e.target.value))}
          />
          <p className="admin-help-text">× min_buy_in; default {defaults.buy_in_multiplier}</p>
        </div>
        <div className="admin-form-group">
          <label className="admin-label" htmlFor="stake_comfort_zone">
            Stake comfort zone
          </label>
          <select
            id="stake_comfort_zone"
            className="admin-input"
            value={knobs.stake_comfort_zone}
            onChange={(e) => updateField('stake_comfort_zone', e.target.value)}
          >
            {STAKE_COMFORT_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <p className="admin-help-text">v2 — unused in v1</p>
        </div>
      </div>

      <div style={{ marginTop: 'var(--space-3)' }}>
        <button
          type="button"
          className="admin-btn admin-btn--primary"
          onClick={handleSave}
          disabled={saving || !hasChanges}
        >
          {saving ? 'Saving…' : hasChanges ? 'Save Bankroll Knobs' : 'Saved'}
        </button>
      </div>
    </div>
  );
}

// ============================================
// StakingProfileSection (cash mode admin)
// ============================================

interface BorrowerProfileResponse {
  success?: boolean;
  name?: string;
  personality_id?: string;
  willing?: boolean;
  /** Effective threshold the staking engine uses — explicit override
   *  if set in config_json, else ego-derived. */
  willingness_threshold?: number;
  /** The explicit override from config_json, or null if none set
   *  (i.e. ego derivation is in effect). */
  willingness_threshold_explicit?: number | null;
  /** What ego derivation would yield, regardless of override. Powers
   *  the "Use ego-derived default" reset button. */
  ego_derived_threshold?: number;
  ego?: number;
  defaults?: { willing: boolean; willingness_threshold: number };
  error?: string;
}

interface StakingProfileSectionProps {
  personalityName: string;
  showAlert: (type: AlertState['type'], message: string) => void;
}

/** Render the per-personality borrower profile editor — the AI's
 *  willingness to BE staked by a player, and the relationship-axes
 *  trust threshold they require before accepting.
 *
 *  The threshold has two states the admin needs to think about:
 *    - Explicit override: stored in config_json.borrower_profile.
 *      Hand-tuned per-personality for special cases.
 *    - Ego-derived: computed at load time from anchors.ego when no
 *      override is set. Default for the bulk of the roster.
 *
 *  This section makes that distinction visible: show the effective
 *  value, mark whether it's overridden, and offer a one-click reset
 *  to clear the override and revert to ego-derived. */
function StakingProfileSection({ personalityName, showAlert }: StakingProfileSectionProps) {
  const [data, setData] = useState<BorrowerProfileResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  /** Local edit buffer — separate from `data` so we can show
   *  dirty/pristine state and reset cleanly on a discard. */
  const [editing, setEditing] = useState<{
    willing: boolean;
    threshold: number;
    /** null = clear override (use ego-derived); number = explicit override */
    threshold_override: number | null;
  } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await adminFetch(
        `/api/personality/${encodeURIComponent(personalityName)}/borrower-profile`
      );
      const body = (await response.json()) as BorrowerProfileResponse;
      if (body.success && body.willing !== undefined && body.willingness_threshold !== undefined) {
        setData(body);
        setEditing({
          willing: body.willing,
          threshold: body.willingness_threshold,
          threshold_override: body.willingness_threshold_explicit ?? null,
        });
      } else {
        showAlert('error', body.error || 'Failed to load staking profile');
      }
    } catch (e) {
      logger.error('Failed to load borrower profile', e);
      showAlert('error', 'Error loading staking profile');
    } finally {
      setLoading(false);
    }
  }, [personalityName, showAlert]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSave = useCallback(async () => {
    if (!editing) return;
    setSaving(true);
    try {
      const response = await adminFetch(
        `/api/personality/${encodeURIComponent(personalityName)}/borrower-profile`,
        {
          method: 'PUT',
          body: JSON.stringify({
            willing: editing.willing,
            // Send null when the admin chose "ego-derived" — that
            // clears the explicit override server-side.
            willingness_threshold: editing.threshold_override,
          }),
        }
      );
      const body = (await response.json()) as BorrowerProfileResponse;
      if (body.success && body.willing !== undefined && body.willingness_threshold !== undefined) {
        setData(body);
        setEditing({
          willing: body.willing,
          threshold: body.willingness_threshold,
          threshold_override: body.willingness_threshold_explicit ?? null,
        });
        showAlert('success', 'Staking profile saved');
      } else {
        showAlert('error', body.error || 'Failed to save staking profile');
      }
    } catch (e) {
      logger.error('Failed to save borrower profile', e);
      showAlert('error', 'Error saving staking profile');
    } finally {
      setSaving(false);
    }
  }, [editing, personalityName, showAlert]);

  if (loading) {
    return <p className="admin-help-text">Loading staking profile…</p>;
  }
  if (!data || !editing) {
    return <p className="admin-help-text">No staking profile data available.</p>;
  }

  const isOverride = editing.threshold_override !== null;
  const egoDerived = data.ego_derived_threshold ?? 0.3;
  const hasChanges =
    editing.willing !== data.willing ||
    editing.threshold_override !== (data.willingness_threshold_explicit ?? null);

  // Effective threshold = override if set, else ego-derived. Keep the
  // slider showing the OVERRIDE value while editing so the admin sees
  // exactly what they're committing.
  const effectiveValue = isOverride ? (editing.threshold_override as number) : egoDerived;

  return (
    <div className="pm-bankroll-knobs">
      <p className="admin-help-text" style={{ marginTop: 0 }}>
        Whether this personality accepts stakes from the player and how much goodwill they need
        before saying yes.
      </p>

      <label
        className="admin-checkbox-row"
        style={{ display: 'flex', alignItems: 'center', gap: 8 }}
      >
        <input
          type="checkbox"
          checked={editing.willing}
          onChange={(e) => setEditing({ ...editing, willing: e.target.checked })}
          disabled={saving}
        />
        <span>Accepts stakes from players</span>
      </label>
      <p className="admin-help-text" style={{ marginTop: 4, marginBottom: 16 }}>
        Stoic / principled personalities (Lincoln, Buddha) refuse outright. Unchecking blocks stake
        offers regardless of the threshold below.
      </p>

      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          marginBottom: 4,
        }}
      >
        <label htmlFor="willingness-threshold-slider" style={{ fontWeight: 600 }}>
          Willingness threshold
        </label>
        <span style={{ fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>
          {effectiveValue.toFixed(2)}
          {!isOverride && (
            <span style={{ marginLeft: 6, fontSize: 11, color: '#aaa', fontWeight: 'normal' }}>
              (derived from ego)
            </span>
          )}
          {isOverride && (
            <span style={{ marginLeft: 6, fontSize: 11, color: '#ffd87d', fontWeight: 'normal' }}>
              (override)
            </span>
          )}
        </span>
      </div>
      <input
        id="willingness-threshold-slider"
        type="range"
        min={10}
        max={60}
        step={1}
        value={Math.round(effectiveValue * 100)}
        onChange={(e) => {
          const v = Number(e.target.value) / 100;
          setEditing({ ...editing, threshold_override: v, threshold: v });
        }}
        disabled={saving || !editing.willing}
        style={{ width: '100%' }}
      />
      <div
        style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#aaa' }}
      >
        <span>0.10 (easy)</span>
        <span>0.60 (selective)</span>
      </div>
      <p className="admin-help-text" style={{ marginTop: 8, fontSize: 11.5 }}>
        Score = likability × 0.5 + respect × 0.4 − heat × 0.3. The AI accepts iff score &gt;
        threshold (plus a cut penalty when the offer's cut is steep, minus a desperation relief when
        broke and proud).
      </p>

      <div
        style={{
          marginTop: 12,
          padding: '8px 10px',
          background: 'rgba(0,0,0,0.18)',
          borderRadius: 6,
          fontSize: 12,
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span style={{ color: '#aaa' }}>Ego anchor</span>
          <span style={{ fontVariantNumeric: 'tabular-nums' }}>{(data.ego ?? 0.5).toFixed(2)}</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span style={{ color: '#aaa' }}>Ego-derived default</span>
          <span style={{ fontVariantNumeric: 'tabular-nums' }}>{egoDerived.toFixed(2)}</span>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
        <button
          type="button"
          className="admin-btn admin-btn--secondary"
          onClick={() =>
            setEditing({ ...editing, threshold_override: null, threshold: egoDerived })
          }
          disabled={saving || !isOverride}
          title="Drop the explicit override; threshold will derive from ego."
        >
          Use ego-derived default
        </button>
        <button
          type="button"
          className="admin-btn admin-btn--secondary"
          onClick={() => {
            // Revert local edits to last-saved state.
            setEditing({
              willing: data.willing ?? true,
              threshold: data.willingness_threshold ?? 0.3,
              threshold_override: data.willingness_threshold_explicit ?? null,
            });
          }}
          disabled={saving || !hasChanges}
        >
          Discard changes
        </button>
        <button
          type="button"
          className="admin-btn admin-btn--primary"
          onClick={() => void handleSave()}
          disabled={saving || !hasChanges}
          style={{ marginLeft: 'auto' }}
        >
          {saving ? 'Saving…' : 'Save staking profile'}
        </button>
      </div>
    </div>
  );
}

// ============================================
// StakerSideProfileSection (cash mode admin)
// ============================================

interface StakerProfileShape {
  willing: boolean;
  max_loan_pct_of_bankroll: number;
  floor_anchor: number;
  rate_anchor: number;
  respect_floor: number;
  heat_ceiling: number;
}

interface StakerProfileResponse {
  success?: boolean;
  name?: string;
  personality_id?: string;
  /** Effective profile (per-field fallback to STAKER_PROFILE_DEFAULTS). */
  profile?: StakerProfileShape;
  /** Sub-dict actually stored in config_json — used to detect which
   *  fields are hand-tuned vs defaulted. null when the personality
   *  has no explicit staker_profile (everything is using the default). */
  explicit?: Partial<StakerProfileShape> | null;
  defaults?: StakerProfileShape;
  error?: string;
}

interface StakerSideProfileSectionProps {
  personalityName: string;
  showAlert: (type: AlertState['type'], message: string) => void;
}

/** Render the per-personality STAKER profile editor — what this AI
 *  offers when OTHER players ask them for a stake-up loan. Mirrors
 *  the borrower side (`StakingProfileSection`) but with the six lender
 *  knobs instead of the willingness threshold.
 *
 *  No ego-derivation here — every field is either explicitly set in
 *  config_json or falls back to STAKER_PROFILE_DEFAULTS at load time.
 *  The route does a full-replacement PUT, so saving writes all six
 *  fields whether they were tuned or not. */
function StakerSideProfileSection({ personalityName, showAlert }: StakerSideProfileSectionProps) {
  const [data, setData] = useState<StakerProfileResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<StakerProfileShape | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await adminFetch(
        `/api/personality/${encodeURIComponent(personalityName)}/staker-profile`
      );
      const body = (await response.json()) as StakerProfileResponse;
      if (body.success && body.profile) {
        setData(body);
        setEditing({ ...body.profile });
      } else {
        showAlert('error', body.error || 'Failed to load staker profile');
      }
    } catch (e) {
      logger.error('Failed to load staker profile', e);
      showAlert('error', 'Error loading staker profile');
    } finally {
      setLoading(false);
    }
  }, [personalityName, showAlert]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSave = useCallback(async () => {
    if (!editing) return;
    setSaving(true);
    try {
      const response = await adminFetch(
        `/api/personality/${encodeURIComponent(personalityName)}/staker-profile`,
        { method: 'PUT', body: JSON.stringify(editing) }
      );
      const body = (await response.json()) as StakerProfileResponse;
      if (body.success && body.profile) {
        setData(body);
        setEditing({ ...body.profile });
        showAlert('success', 'Staker profile saved');
      } else {
        showAlert('error', body.error || 'Failed to save staker profile');
      }
    } catch (e) {
      logger.error('Failed to save staker profile', e);
      showAlert('error', 'Error saving staker profile');
    } finally {
      setSaving(false);
    }
  }, [editing, personalityName, showAlert]);

  if (loading) {
    return <p className="admin-help-text">Loading staker profile…</p>;
  }
  if (!data || !editing || !data.profile || !data.defaults) {
    return <p className="admin-help-text">No staker profile data available.</p>;
  }

  const hasChanges =
    editing.willing !== data.profile.willing ||
    editing.max_loan_pct_of_bankroll !== data.profile.max_loan_pct_of_bankroll ||
    editing.floor_anchor !== data.profile.floor_anchor ||
    editing.rate_anchor !== data.profile.rate_anchor ||
    editing.respect_floor !== data.profile.respect_floor ||
    editing.heat_ceiling !== data.profile.heat_ceiling;

  // Helper — is `field` explicitly tuned in config_json, or defaulted?
  const isExplicit = (field: keyof StakerProfileShape): boolean => {
    return !!(data.explicit && field in data.explicit);
  };

  // Reusable slider+number row. Centralized so all six knobs share
  // the same layout/copy structure without 6x repetition.
  const KnobRow = ({
    label,
    field,
    min,
    max,
    step,
    hint,
  }: {
    label: string;
    field: keyof Omit<StakerProfileShape, 'willing'>;
    min: number;
    max: number;
    step: number;
    hint: string;
  }) => (
    <div style={{ marginBottom: 12 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          marginBottom: 4,
        }}
      >
        <label style={{ fontWeight: 600 }}>{label}</label>
        <span style={{ fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>
          {(editing[field] as number).toFixed(2)}
          {!isExplicit(field) && (
            <span style={{ marginLeft: 6, fontSize: 11, color: '#aaa', fontWeight: 'normal' }}>
              (default)
            </span>
          )}
        </span>
      </div>
      <input
        type="range"
        min={min * 100}
        max={max * 100}
        step={step * 100}
        value={Math.round((editing[field] as number) * 100)}
        onChange={(e) => {
          const v = Number(e.target.value) / 100;
          setEditing({ ...editing, [field]: v });
        }}
        disabled={saving || !editing.willing}
        style={{ width: '100%' }}
      />
      <p className="admin-help-text" style={{ marginTop: 2, marginBottom: 0, fontSize: 11.5 }}>
        {hint}
      </p>
    </div>
  );

  return (
    <div className="pm-bankroll-knobs">
      <p className="admin-help-text" style={{ marginTop: 0 }}>
        How this personality behaves when ANOTHER player (AI or human) asks them for a stake-up
        loan. Six knobs that shape the offer terms and the relationship gates that have to clear
        before they'll lend.
      </p>

      <label
        className="admin-checkbox-row"
        style={{ display: 'flex', alignItems: 'center', gap: 8 }}
      >
        <input
          type="checkbox"
          checked={editing.willing}
          onChange={(e) => setEditing({ ...editing, willing: e.target.checked })}
          disabled={saving}
        />
        <span>Willing to stake other players</span>
      </label>
      <p className="admin-help-text" style={{ marginTop: 4, marginBottom: 16 }}>
        Unchecking blocks all offers from this personality regardless of the knobs below. Chaos /
        hostile personalities (Mime, Cheshire Cat) refuse outright.
      </p>

      <KnobRow
        label="Max loan (% of bankroll)"
        field="max_loan_pct_of_bankroll"
        min={0}
        max={0.3}
        step={0.01}
        hint="Largest loan size as a fraction of their projected bankroll. Generous = 0.10–0.20, cautious = 0.03–0.07."
      />
      <KnobRow
        label="Floor anchor (repayment multiple)"
        field="floor_anchor"
        min={1.0}
        max={2.0}
        step={0.05}
        hint="Repayment floor multiple — 1.00 = par, 1.20 = +20%. Saintly = 1.00–1.10, sharks = 1.30–1.50."
      />
      <KnobRow
        label="Rate anchor (cut after floor)"
        field="rate_anchor"
        min={0}
        max={0.6}
        step={0.01}
        hint="Sponsor's cut of post-floor winnings. Gentle = 0.10–0.20, ruthless = 0.35–0.50."
      />
      <KnobRow
        label="Respect floor"
        field="respect_floor"
        min={-1.0}
        max={1.0}
        step={0.05}
        hint="Minimum relationship-respect needed before lending. More negative = lends to almost anyone."
      />
      <KnobRow
        label="Heat ceiling"
        field="heat_ceiling"
        min={0}
        max={1.0}
        step={0.05}
        hint="Maximum active conflict tolerated while lending. 1.00 = never refuses on heat alone; 0.00 = any heat blocks."
      />

      <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
        <button
          type="button"
          className="admin-btn admin-btn--secondary"
          onClick={() => data.defaults && setEditing({ ...data.defaults })}
          disabled={saving}
          title="Reset all knobs to STAKER_PROFILE_DEFAULTS."
        >
          Reset to defaults
        </button>
        <button
          type="button"
          className="admin-btn admin-btn--secondary"
          onClick={() => data.profile && setEditing({ ...data.profile })}
          disabled={saving || !hasChanges}
        >
          Discard changes
        </button>
        <button
          type="button"
          className="admin-btn admin-btn--primary"
          onClick={() => void handleSave()}
          disabled={saving || !hasChanges}
          style={{ marginLeft: 'auto' }}
        >
          {saving ? 'Saving…' : 'Save staker profile'}
        </button>
      </div>
    </div>
  );
}

// ============================================
// Main Component
// ============================================

interface PersonalityManagerProps {
  onBack?: () => void;
  embedded?: boolean;
}

export function PersonalityManager({ onBack, embedded = false }: PersonalityManagerProps) {
  // Responsive breakpoints
  const { isDesktop, isTablet, isMobile } = useViewport();

  // Core state
  const [personalities, setPersonalities] = useState<Record<string, PersonalityData>>({});
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [formData, setFormData] = useState<PersonalityData | null>(null);
  const [originalData, setOriginalData] = useState<PersonalityData | null>(null);

  // Categories and metadata
  const [categories, setCategories] = useState<Record<string, string[]>>({
    standard: [],
    mine: [],
  });
  const [personalityMeta, setPersonalityMeta] = useState<
    Record<string, { visibility?: string; owner_id?: string }>
  >({});
  const [isAdmin, setIsAdmin] = useState(false);
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);

  // UI state
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [alert, setAlert] = useState<AlertState | null>(null);
  const [modal, setModal] = useState<ModalState>({ type: null });
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [masterSearch, setMasterSearch] = useState('');
  const [masterPanelOpen, setMasterPanelOpen] = useState(false);

  // Accordion state
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({
    basic: true,
    anchors: false,
    tics: false,
    avatar: false,
    bankroll: false,
    staking: false,
    stakerProfile: false,
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
      const response = await fetch(`${config.API_URL}/api/personalities`, {
        credentials: 'include',
      });
      const data = await response.json();
      if (data.success) {
        setPersonalities(data.personalities);
        if (data.categories) setCategories(data.categories);
        if (data.metadata) setPersonalityMeta(data.metadata);
        if (data.is_admin !== undefined) setIsAdmin(data.is_admin);
        if (data.user_id) setCurrentUserId(data.user_id);
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
    setOpenSections((prev) => ({ ...prev, [section]: !prev[section] }));
  };

  const selectPersonality = useCallback(
    (name: string) => {
      const data = personalities[name];
      if (data) {
        setSelectedName(name);
        setFormData({ ...data });
        setOriginalData({ ...data });
        // Open basic section by default
        setOpenSections((prev) => ({ ...prev, basic: true }));
      }
    },
    [personalities]
  );

  const updateFormData = useCallback((updates: Partial<PersonalityData>) => {
    setFormData((prev) => (prev ? { ...prev, ...updates } : null));
  }, []);

  const updateAnchor = useCallback((field: keyof PersonalityAnchors, value: number) => {
    setFormData((prev) => {
      if (!prev) return null;
      return {
        ...prev,
        anchors: {
          ...getDefaultAnchors(),
          ...prev.anchors,
          [field]: value,
        },
      };
    });
  }, []);

  const handleVisibilityChange = async (newVisibility: string) => {
    if (!selectedName) return;
    try {
      const response = await fetch(
        `${config.API_URL}/api/personality/${encodeURIComponent(selectedName)}/visibility`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ visibility: newVisibility }),
        }
      );
      const data = await response.json();
      if (data.success) {
        showAlert('success', `${selectedName} is now ${newVisibility}`);
        setPersonalityMeta((prev) => ({
          ...prev,
          [selectedName]: { ...prev[selectedName], visibility: newVisibility },
        }));
        // Re-categorize: move personality between categories
        setCategories((prev) => {
          const updated: Record<string, string[]> = {};
          for (const [key, names] of Object.entries(prev)) {
            updated[key] = names.filter((n) => n !== selectedName);
          }
          const targetCategory =
            newVisibility === 'disabled'
              ? 'disabled'
              : newVisibility === 'private'
                ? 'mine'
                : 'standard';
          if (!updated[targetCategory]) updated[targetCategory] = [];
          updated[targetCategory].push(selectedName);
          return updated;
        });
      } else {
        showAlert('error', data.error || 'Failed to update visibility');
      }
    } catch {
      showAlert('error', 'Error updating visibility');
    }
  };

  const handleSave = async () => {
    if (!selectedName || !formData) return;

    setSaving(true);
    try {
      const response = await fetch(`${config.API_URL}/api/personality/${selectedName}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(formData),
      });
      const data = await response.json();

      if (data.success) {
        showAlert('success', `Saved ${selectedName} successfully`);
        setPersonalities((prev) => ({ ...prev, [selectedName]: formData }));
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
        method: 'DELETE',
        credentials: 'include',
      });
      const data = await response.json();

      if (data.success) {
        showAlert('success', `Deleted ${selectedName}`);
        setPersonalities((prev) => {
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
        credentials: 'include',
        body: JSON.stringify({ name: selectedName, force: true }),
      });
      const data = await response.json();

      if (data.success) {
        showAlert('success', `Regenerated ${selectedName} with AI`);
        setPersonalities((prev) => ({ ...prev, [selectedName]: data.personality }));
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
      anchors: getDefaultAnchors(),
      verbal_tics: [],
      physical_tics: [],
    };

    setPersonalities((prev) => ({ ...prev, [name]: newPersonality }));
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
        credentials: 'include',
        body: JSON.stringify({ name }),
      });
      const data = await response.json();

      if (data.success) {
        setPersonalities((prev) => ({ ...prev, [name]: data.personality }));
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
      const response = await fetch(
        `${config.API_URL}/api/personality/${selectedName}/avatar-description`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ avatar_description: formData.avatar_description || '' }),
        }
      );
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

  const hasChanges =
    formData && originalData && JSON.stringify(formData) !== JSON.stringify(originalData);

  // Build grouped character list: mine first, then standard, then disabled (admin only)
  const characterGroups = useMemo(() => {
    const groups: { label: string; names: string[] }[] = [];
    const mine = (categories.mine || []).slice().sort();
    const standard = (categories.standard || []).slice().sort();
    const disabled = (categories.disabled || []).slice().sort();
    if (mine.length > 0) groups.push({ label: 'My Characters', names: mine });
    if (standard.length > 0) groups.push({ label: 'Standard', names: standard });
    if (disabled.length > 0) groups.push({ label: 'Disabled', names: disabled });
    return groups;
  }, [categories]);

  const characterNames = useMemo(() => characterGroups.flatMap((g) => g.names), [characterGroups]);

  // Safely merge API data with defaults to handle missing/partial data
  const anchors: PersonalityAnchors = {
    ...getDefaultAnchors(),
    ...(formData?.anchors || {}),
  };

  const archetype = classifyArchetype(anchors.baseline_looseness, anchors.baseline_aggression);

  // Editor sections (scrollable content)
  const editorSections =
    selectedName && formData ? (
      <div className="pm-sections">
        {/* Basic Info */}
        <CollapsibleSection
          title="Basic Info"
          icon={<User size={20} />}
          isOpen={openSections.basic}
          onToggle={() => toggleSection('basic')}
        >
          <div className="admin-form-group">
            <label className="admin-label" htmlFor="play_style">
              Play Style
            </label>
            <input
              id="play_style"
              type="text"
              className="admin-input"
              value={formData.play_style || ''}
              onChange={(e) => updateFormData({ play_style: e.target.value })}
              placeholder="e.g., aggressive and boastful"
            />
          </div>
          <div className="admin-form-row">
            <div className="admin-form-group">
              <label className="admin-label" htmlFor="confidence">
                Confidence
              </label>
              <input
                id="confidence"
                type="text"
                className="admin-input"
                value={formData.default_confidence || ''}
                onChange={(e) => updateFormData({ default_confidence: e.target.value })}
                placeholder="e.g., supreme"
              />
            </div>
            <div className="admin-form-group">
              <label className="admin-label" htmlFor="attitude">
                Attitude
              </label>
              <input
                id="attitude"
                type="text"
                className="admin-input"
                value={formData.default_attitude || ''}
                onChange={(e) => updateFormData({ default_attitude: e.target.value })}
                placeholder="e.g., domineering"
              />
            </div>
          </div>
        </CollapsibleSection>

        {/* Psychology Anchors */}
        <CollapsibleSection
          title="Psychology Anchors"
          icon={<Brain size={20} />}
          isOpen={openSections.anchors}
          onToggle={() => toggleSection('anchors')}
          badge={archetype.label}
        >
          <p className="admin-help-text" style={{ marginTop: 0, marginBottom: 'var(--space-4)' }}>
            These anchors control poker behavior and archetype classification.
          </p>

          <h4 className="pm-anchor-group-title">Play Style</h4>
          <TraitSlider
            id="baseline_looseness"
            label="Tight → Loose"
            value={anchors.baseline_looseness}
            elasticity={0}
            onChange={(v) => updateAnchor('baseline_looseness', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <p className="admin-help-text">Hand range width — how many hands to play</p>
          <TraitSlider
            id="baseline_aggression"
            label="Passive → Aggressive"
            value={anchors.baseline_aggression}
            elasticity={0}
            onChange={(v) => updateAnchor('baseline_aggression', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <p className="admin-help-text">Bet/raise frequency</p>

          <h4 className="pm-anchor-group-title">Psychology</h4>
          <TraitSlider
            id="ego"
            label="Stable → Fragile"
            value={anchors.ego}
            elasticity={0}
            onChange={(v) => updateAnchor('ego', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <p className="admin-help-text">Confidence brittleness after losses</p>
          <TraitSlider
            id="poise"
            label="Volatile → Composed"
            value={anchors.poise}
            elasticity={0}
            onChange={(v) => updateAnchor('poise', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <p className="admin-help-text">Composure resistance to tilt</p>
          <TraitSlider
            id="expressiveness"
            label="Poker Face → Open Book"
            value={anchors.expressiveness}
            elasticity={0}
            onChange={(v) => updateAnchor('expressiveness', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <p className="admin-help-text">Emotional transparency in chat</p>

          <h4 className="pm-anchor-group-title">Behavior</h4>
          <TraitSlider
            id="risk_identity"
            label="Risk-Averse → Risk-Seeking"
            value={anchors.risk_identity}
            elasticity={0}
            onChange={(v) => updateAnchor('risk_identity', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <TraitSlider
            id="adaptation_bias"
            label="Static → Adaptive"
            value={anchors.adaptation_bias}
            elasticity={0}
            onChange={(v) => updateAnchor('adaptation_bias', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <TraitSlider
            id="baseline_energy"
            label="Reserved → Animated"
            value={anchors.baseline_energy}
            elasticity={0}
            onChange={(v) => updateAnchor('baseline_energy', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <TraitSlider
            id="recovery_rate"
            label="Slow Recovery → Fast Recovery"
            value={anchors.recovery_rate}
            elasticity={0}
            onChange={(v) => updateAnchor('recovery_rate', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <p className="admin-help-text">How quickly mood returns to baseline</p>
        </CollapsibleSection>

        {/* Verbal & Physical Tics */}
        <CollapsibleSection
          title="Quirks & Tics"
          icon={<MessageCircle size={20} />}
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
          icon={<ImageIcon size={20} />}
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

        {/* Cash-mode Bankroll Knobs (admin only) */}
        {isAdmin && (
          <CollapsibleSection
            title="Bankroll Knobs"
            icon={<Coins size={20} />}
            isOpen={openSections.bankroll}
            onToggle={() => toggleSection('bankroll')}
          >
            <BankrollKnobsSection personalityName={selectedName} showAlert={showAlert} />
          </CollapsibleSection>
        )}

        {/* Staking Profile — Borrower side (admin only): do they
            accept stakes, and at what trust threshold. */}
        {isAdmin && (
          <CollapsibleSection
            title="Staking Profile — Borrower"
            icon={<Coins size={20} />}
            isOpen={openSections.staking}
            onToggle={() => toggleSection('staking')}
          >
            <StakingProfileSection personalityName={selectedName} showAlert={showAlert} />
          </CollapsibleSection>
        )}

        {/* Staking Profile — Staker side (admin only): what loan
            terms they offer when OTHERS ask them for a stake-up. */}
        {isAdmin && (
          <CollapsibleSection
            title="Staking Profile — Staker"
            icon={<Coins size={20} />}
            isOpen={openSections.stakerProfile}
            onToggle={() => toggleSection('stakerProfile')}
          >
            <StakerSideProfileSection personalityName={selectedName} showAlert={showAlert} />
          </CollapsibleSection>
        )}
      </div>
    ) : null;

  // Action bar (fixed at bottom)
  const actionBar =
    selectedName && formData ? (
      <div className={isMobile ? 'pm-actions' : 'admin-detail__footer'}>
        <div className={isMobile ? 'pm-actions__secondary' : 'admin-detail__footer-secondary'}>
          <button
            type="button"
            className="admin-btn admin-btn--secondary"
            onClick={() => setModal({ type: 'regenerate' })}
            disabled={saving}
          >
            ✨ AI Regen
          </button>
          <button
            type="button"
            className="admin-btn admin-btn--danger"
            onClick={() => setModal({ type: 'delete' })}
            disabled={saving}
          >
            Delete
          </button>
        </div>
        <div className={isMobile ? 'pm-actions__primary' : 'admin-detail__footer-primary'}>
          {hasChanges && (
            <button
              type="button"
              className="admin-btn admin-btn--secondary"
              onClick={handleCancel}
              disabled={saving}
            >
              Cancel
            </button>
          )}
          <button
            type="button"
            className="admin-btn admin-btn--primary"
            onClick={handleSave}
            disabled={saving || !hasChanges}
          >
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
        </div>
      </div>
    ) : null;

  // Empty state content
  const emptyContent = (
    <div className={isTablet ? 'admin-detail__empty' : 'admin-empty'}>
      <div
        className={isTablet ? 'admin-detail__empty-icon' : 'admin-empty__icon'}
        style={{ fontSize: '64px', opacity: 0.5 }}
      >
        🎭
      </div>
      <h3 className={isTablet ? 'admin-detail__empty-title' : 'admin-empty__title'}>
        No Character Selected
      </h3>
      <p className={isTablet ? 'admin-detail__empty-description' : 'admin-empty__description'}>
        {isTablet
          ? 'Select a character from the list or create a new one'
          : 'Choose a character above or create a new one'}
      </p>
      <button
        type="button"
        className="admin-btn admin-btn--primary admin-btn--lg"
        onClick={() => setModal({ type: 'create' })}
      >
        Create New Character
      </button>
    </div>
  );

  const content = (
    <>
      {/* Alert Toast */}
      {alert && (
        <div className="admin-toast-container">
          <div className={`admin-alert admin-alert--${alert.type}`}>
            <span className="admin-alert__icon">
              {alert.type === 'success' && '✓'}
              {alert.type === 'error' && '✕'}
              {alert.type === 'info' && 'ℹ'}
            </span>
            <span className="admin-alert__content">{alert.message}</span>
            <button className="admin-alert__dismiss" onClick={() => setAlert(null)}>
              ×
            </button>
          </div>
        </div>
      )}

      {/* Loading State */}
      {loading ? (
        <div className="admin-loading">
          <div className="admin-loading__spinner" />
          <span className="admin-loading__text">Loading personalities...</span>
        </div>
      ) : !isMobile ? (
        /* ==========================================
           TABLET & DESKTOP: Master-Detail Layout
           ========================================== */
        <div className="admin-master-detail">
          {/* Master Panel (sidebar) */}
          <aside
            className={`admin-master ${masterPanelOpen || isDesktop ? 'admin-master--open' : ''}`}
          >
            <MasterList
              characters={characterNames}
              groups={characterGroups}
              selected={selectedName}
              onSelect={(name) => {
                selectPersonality(name);
                if (!isDesktop) setMasterPanelOpen(false);
              }}
              onCreate={() => {
                setMasterPanelOpen(false);
                setModal({ type: 'create' });
              }}
              search={masterSearch}
              onSearchChange={setMasterSearch}
              personalityMeta={personalityMeta}
            />
          </aside>

          {/* Detail Panel */}
          <main className="admin-detail">
            {/* Tablet toggle button (hidden on desktop) */}
            {!isDesktop && (
              <button
                type="button"
                className="admin-master-toggle"
                onClick={() => setMasterPanelOpen(!masterPanelOpen)}
              >
                <MenuIcon />
                <span>{selectedName || 'Select Character'}</span>
              </button>
            )}

            {/* Detail header when character selected */}
            {selectedName &&
              formData &&
              (() => {
                const meta = personalityMeta[selectedName];
                const currentVis = meta?.visibility || 'public';
                const isOwner = !!currentUserId && meta?.owner_id === currentUserId;
                const canChangeVisibility = isAdmin || isOwner;
                // PRH-27: publishing is admin-only. A non-admin owner can keep
                // their personality private (or un-publish a legacy public one)
                // but can't make it public — the server rejects it too.
                const visibilityOptions: readonly ('public' | 'private' | 'disabled')[] = isAdmin
                  ? ['public', 'private', 'disabled']
                  : ['private'];
                return (
                  <div className="admin-detail__header">
                    <div>
                      <h2 className="admin-detail__title">{selectedName}</h2>
                      <p className="admin-detail__subtitle">
                        {formData.play_style || 'No play style defined'}
                      </p>
                    </div>
                    {canChangeVisibility && (
                      <div className="pm-visibility-toggle">
                        {visibilityOptions.map((vis) => (
                          <button
                            key={vis}
                            type="button"
                            className={`pm-visibility-toggle__btn pm-visibility-toggle__btn--${vis} ${currentVis === vis ? 'pm-visibility-toggle__btn--active' : ''}`}
                            onClick={() => handleVisibilityChange(vis)}
                            disabled={currentVis === vis}
                          >
                            {vis === 'public'
                              ? 'Public'
                              : vis === 'private'
                                ? 'Private'
                                : 'Disabled'}
                          </button>
                        ))}
                      </div>
                    )}
                    {!canChangeVisibility && currentVis !== 'public' && (
                      <span className={`pm-visibility-badge pm-visibility-badge--${currentVis}`}>
                        {currentVis}
                      </span>
                    )}
                  </div>
                );
              })()}

            {/* Detail content (scrollable) */}
            <div className="admin-detail__content">{editorSections || emptyContent}</div>

            {/* Action bar (fixed at bottom) */}
            {actionBar}
          </main>

          {/* Backdrop for tablet sidebar */}
          {!isDesktop && masterPanelOpen && (
            <div
              className="pm-sheet-backdrop pm-sheet-backdrop--visible"
              onClick={() => setMasterPanelOpen(false)}
            />
          )}
        </div>
      ) : (
        /* ==========================================
           MOBILE: Original Bottom Sheet Layout
           ========================================== */
        <div className={`pm-container${embedded ? ' pm-container--embedded' : ''}`}>
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
                    <circle cx="10" cy="6" r="4" stroke="currentColor" strokeWidth="1.5" />
                    <path
                      d="M3 18C3 14.134 6.134 11 10 11C13.866 11 17 14.134 17 18"
                      stroke="currentColor"
                      strokeWidth="1.5"
                      strokeLinecap="round"
                    />
                  </svg>
                </span>
                <span className="pm-selector-trigger__placeholder">Select a character to edit</span>
              </>
            )}
            <svg
              className="pm-selector-trigger__chevron"
              width="20"
              height="20"
              viewBox="0 0 20 20"
              fill="none"
            >
              <path
                d="M5 7.5L10 12.5L15 7.5"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>

          {/* Character Selector Bottom Sheet */}
          <CharacterSelector
            characters={characterNames}
            groups={characterGroups}
            selected={selectedName}
            onSelect={selectPersonality}
            onCreate={() => {
              setSelectorOpen(false);
              setModal({ type: 'create' });
            }}
            isOpen={selectorOpen}
            onClose={() => setSelectorOpen(false)}
            personalityMeta={personalityMeta}
          />

          {/* Editor or Empty State */}
          {selectedName && formData ? (
            <div className="pm-editor">
              {editorSections}
              {actionBar}
            </div>
          ) : (
            emptyContent
          )}

          {/* Floating Create Button */}
          <button
            type="button"
            className="pm-fab"
            onClick={() => setModal({ type: 'create' })}
            aria-label="Create new character"
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
              <path
                d="M12 5V19M5 12H19"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
              />
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
    </>
  );

  // If embedded, return content directly without PageLayout wrapper
  if (embedded) {
    return content;
  }

  // Otherwise wrap in PageLayout
  return (
    <PageLayout variant="top" glowColor="gold" maxWidth="lg">
      <PageHeader
        title="Character Manager"
        subtitle="Create and customize AI opponents"
        onBack={onBack}
        titleVariant="primary"
      />
      {content}
    </PageLayout>
  );
}

// ============================================
// Helpers
// ============================================

function getDefaultAnchors(): PersonalityAnchors {
  return {
    baseline_aggression: 0.5,
    baseline_looseness: 0.3,
    ego: 0.5,
    poise: 0.7,
    expressiveness: 0.5,
    risk_identity: 0.5,
    adaptation_bias: 0.5,
    baseline_energy: 0.5,
    recovery_rate: 0.15,
  };
}

function classifyArchetype(looseness: number, aggression: number): { key: string; label: string } {
  if (looseness < 0.45) {
    return aggression < 0.5
      ? { key: 'tight_passive', label: 'Rock' }
      : { key: 'tight_aggressive', label: 'TAG' };
  } else if (looseness > 0.65) {
    return aggression < 0.5
      ? { key: 'loose_passive', label: 'Fish' }
      : { key: 'loose_aggressive', label: 'LAG' };
  }
  return { key: 'default', label: 'Balanced' };
}
