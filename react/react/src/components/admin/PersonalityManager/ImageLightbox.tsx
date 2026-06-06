import { useEffect, useRef, useState } from 'react';
import { config } from '../../../config';
import { adminFetch } from '../../../utils/api';
import type { EmotionImage } from './types';

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

export function ImageLightbox({
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
