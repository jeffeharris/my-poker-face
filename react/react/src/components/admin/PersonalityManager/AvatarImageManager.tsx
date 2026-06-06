import { useEffect, useState } from 'react';
import { config } from '../../../config';
import { logger } from '../../../utils/logger';
import type { EmotionImage } from './types';
import { ImageLightbox } from './ImageLightbox';

interface AvatarImageManagerProps {
  personalityName: string;
  avatarDescription: string;
  onDescriptionChange: (desc: string) => void;
  onDescriptionSave: () => Promise<void>;
}

export function AvatarImageManager({
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
