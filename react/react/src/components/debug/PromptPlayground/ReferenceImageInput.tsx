/**
 * Reference Image Input component for img2img generation.
 *
 * Allows users to upload a photo or paste a URL to use as a base image
 * for image-to-image generation (e.g., transforming their photo into an avatar).
 */
import { useState, useCallback, useRef } from 'react';
import { config } from '../../../config';

interface ReferenceImageUploadResponse {
  success: boolean;
  reference_id?: string;
  width?: number;
  height?: number;
  content_type?: string;
  size_bytes?: number;
  error?: string;
}

interface Props {
  value: string | null;  // reference_image_id
  onChange: (id: string | null) => void;
  disabled?: boolean;
}

export function ReferenceImageInput({ value, onChange, disabled }: Props) {
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [urlInput, setUrlInput] = useState('');
  const [showUrlInput, setShowUrlInput] = useState(false);
  const [imagePreview, setImagePreview] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Load preview when value changes
  const loadPreview = useCallback(async (referenceId: string) => {
    try {
      const previewUrl = `${config.API_URL}/admin/api/reference-images/${referenceId}`;
      setImagePreview(previewUrl);
    } catch (err) {
      console.error('Failed to load preview:', err);
    }
  }, []);

  // Handle file upload
  const handleFileUpload = useCallback(async (file: File) => {
    setIsUploading(true);
    setError(null);

    try {
      const formData = new FormData();
      formData.append('file', file);

      const response = await fetch(`${config.API_URL}/admin/api/reference-images`, {
        method: 'POST',
        body: formData,
      });

      const data: ReferenceImageUploadResponse = await response.json();

      if (data.success && data.reference_id) {
        onChange(data.reference_id);
        loadPreview(data.reference_id);
      } else {
        setError(data.error || 'Upload failed');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setIsUploading(false);
    }
  }, [onChange, loadPreview]);

  // Handle URL submission
  const handleUrlSubmit = useCallback(async () => {
    if (!urlInput.trim()) return;

    setIsUploading(true);
    setError(null);

    try {
      const response = await fetch(`${config.API_URL}/admin/api/reference-images`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: urlInput.trim() }),
      });

      const data: ReferenceImageUploadResponse = await response.json();

      if (data.success && data.reference_id) {
        onChange(data.reference_id);
        loadPreview(data.reference_id);
        setUrlInput('');
        setShowUrlInput(false);
      } else {
        setError(data.error || 'Failed to fetch URL');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch URL');
    } finally {
      setIsUploading(false);
    }
  }, [urlInput, onChange, loadPreview]);

  // Handle file input change
  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      handleFileUpload(file);
    }
  }, [handleFileUpload]);

  // Handle drag and drop
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();

    const file = e.dataTransfer.files?.[0];
    if (file && file.type.startsWith('image/')) {
      handleFileUpload(file);
    }
  }, [handleFileUpload]);

  // Clear selection
  const handleClear = useCallback(() => {
    onChange(null);
    setImagePreview(null);
    setUrlInput('');
    setShowUrlInput(false);
    setError(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  }, [onChange]);

  // Load preview when value is set externally
  if (value && !imagePreview) {
    loadPreview(value);
  }

  return (
    <div className="reference-image-input-container">
      {value && imagePreview ? (
        /* Image selected - show preview */
        <div className="reference-image-preview-wrapper">
          <img
            src={imagePreview}
            alt="Reference"
            className="reference-image-preview"
          />
          <button
            type="button"
            className="reference-image-clear"
            onClick={handleClear}
            disabled={disabled}
            title="Remove reference image"
          >
            ×
          </button>
        </div>
      ) : (
        /* No image - show upload area */
        <div
          className={`reference-image-input ${isUploading ? 'uploading' : ''} ${disabled ? 'disabled' : ''}`}
          onDragOver={handleDragOver}
          onDrop={disabled ? undefined : handleDrop}
          onClick={() => !disabled && !showUrlInput && fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            onChange={handleFileChange}
            disabled={disabled || isUploading}
            style={{ display: 'none' }}
          />

          {isUploading ? (
            <div className="upload-status">
              <span className="upload-spinner" />
              <span>Uploading...</span>
            </div>
          ) : showUrlInput ? (
            <div className="url-input-section" onClick={(e) => e.stopPropagation()}>
              <input
                type="url"
                placeholder="https://example.com/image.jpg"
                value={urlInput}
                onChange={(e) => setUrlInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleUrlSubmit()}
                disabled={disabled}
                autoFocus
              />
              <div className="url-input-actions">
                <button
                  type="button"
                  onClick={handleUrlSubmit}
                  disabled={!urlInput.trim() || disabled}
                >
                  Load
                </button>
                <button
                  type="button"
                  onClick={() => { setShowUrlInput(false); setUrlInput(''); }}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="upload-icon">📷</div>
              <div className="upload-text">
                <span className="upload-primary">Drop image here or click to upload</span>
                <span className="upload-secondary">
                  or{' '}
                  <button
                    type="button"
                    className="url-link"
                    onClick={(e) => { e.stopPropagation(); setShowUrlInput(true); }}
                    disabled={disabled}
                  >
                    paste URL
                  </button>
                </span>
              </div>
            </>
          )}
        </div>
      )}

      {error && <div className="reference-image-error">{error}</div>}
    </div>
  );
}

export default ReferenceImageInput;
