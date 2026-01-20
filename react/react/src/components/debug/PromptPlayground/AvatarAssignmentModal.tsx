import { useState, useEffect } from 'react';
import { config } from '../../../config';
import './AvatarAssignmentModal.css';

interface Props {
  imageUrl: string;
  defaultPersonality?: string;
  defaultEmotion?: string;
  captureId: number;
  onAssign: (personality: string, emotion: string) => Promise<void>;
  onClose: () => void;
}

// Default emotions - will be replaced by API fetch
const DEFAULT_EMOTIONS = ['confident', 'happy', 'thinking', 'nervous', 'angry', 'shocked'];

export function AvatarAssignmentModal({
  imageUrl,
  defaultPersonality,
  defaultEmotion,
  captureId,
  onAssign,
  onClose,
}: Props) {
  const [personality, setPersonality] = useState(defaultPersonality || '');
  const [emotion, setEmotion] = useState(defaultEmotion || 'confident');
  const [personalities, setPersonalities] = useState<string[]>([]);
  const [emotions, setEmotions] = useState<string[]>(DEFAULT_EMOTIONS);
  const [currentAvatarUrl, setCurrentAvatarUrl] = useState<string | null>(null);
  const [currentAvatarLoaded, setCurrentAvatarLoaded] = useState(false);
  const [currentAvatarError, setCurrentAvatarError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  // Fetch available personalities and emotions
  useEffect(() => {
    // Fetch personalities
    fetch(`${config.API_URL}/api/personalities`)
      .then((res) => res.json())
      .then((data) => {
        if (data.personalities) {
          // API returns personalities as an object with names as keys
          const names = Object.keys(data.personalities).sort();
          setPersonalities(names);
        }
      })
      .catch((err) => console.error('Failed to fetch personalities:', err));

    // Fetch emotions
    fetch(`${config.API_URL}/api/avatar/emotions`)
      .then((res) => res.json())
      .then((data) => {
        if (data.emotions && Array.isArray(data.emotions)) {
          setEmotions(data.emotions);
        }
      })
      .catch((err) => console.error('Failed to fetch emotions:', err));
  }, []);

  // Fetch current avatar when personality/emotion changes
  useEffect(() => {
    if (personality && emotion) {
      setCurrentAvatarLoaded(false);
      setCurrentAvatarError(false);
      setCurrentAvatarUrl(
        `${config.API_URL}/api/avatar/${encodeURIComponent(personality)}/${emotion}?t=${Date.now()}`
      );
    } else {
      setCurrentAvatarUrl(null);
      setCurrentAvatarLoaded(false);
      setCurrentAvatarError(false);
    }
  }, [personality, emotion]);

  const handleAssign = async () => {
    if (!personality) {
      setError('Please select a personality');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      await onAssign(personality, emotion);
      setSuccess(true);
      // Refresh current avatar preview
      setCurrentAvatarUrl(
        `${config.API_URL}/api/avatar/${encodeURIComponent(personality)}/${emotion}?t=${Date.now()}`
      );
      // Close after short delay to show success
      setTimeout(() => onClose(), 1500);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Assignment failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="avatar-modal-overlay" onClick={onClose}>
      <div className="avatar-modal" onClick={(e) => e.stopPropagation()}>
        <div className="avatar-modal-header">
          <h3>Assign as Avatar</h3>
          <button className="close-btn" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="avatar-modal-content">
          <div className="avatar-comparison">
            <div className="avatar-preview current">
              <h4>Current Avatar</h4>
              <div className="avatar-image-container">
                {currentAvatarUrl && !currentAvatarError && (
                  <img
                    src={currentAvatarUrl}
                    alt="Current avatar"
                    style={{ display: currentAvatarLoaded ? 'block' : 'none' }}
                    onLoad={() => setCurrentAvatarLoaded(true)}
                    onError={() => {
                      setCurrentAvatarError(true);
                      setCurrentAvatarLoaded(false);
                    }}
                  />
                )}
                {currentAvatarUrl && !currentAvatarLoaded && !currentAvatarError && (
                  <div className="no-avatar">Loading...</div>
                )}
                {(!currentAvatarUrl || currentAvatarError) && (
                  <div className="no-avatar">
                    {!personality ? 'Select a personality' : 'No avatar exists'}
                  </div>
                )}
              </div>
            </div>

            <div className="avatar-arrow">→</div>

            <div className="avatar-preview new">
              <h4>New Avatar</h4>
              <div className="avatar-image-container">
                <img src={imageUrl} alt="New avatar" />
              </div>
            </div>
          </div>

          <div className="avatar-form">
            <div className="form-row">
              <label>Personality</label>
              <select
                value={personality}
                onChange={(e) => setPersonality(e.target.value)}
                disabled={loading}
              >
                <option value="">Select personality...</option>
                {personalities.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-row">
              <label>Emotion</label>
              <select
                value={emotion}
                onChange={(e) => setEmotion(e.target.value)}
                disabled={loading}
              >
                {emotions.map((e) => (
                  <option key={e} value={e}>
                    {e.charAt(0).toUpperCase() + e.slice(1)}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {error && <div className="avatar-error">{error}</div>}
          {success && (
            <div className="avatar-success">
              Avatar assigned successfully!
            </div>
          )}
        </div>

        <div className="avatar-modal-actions">
          <button className="cancel-btn" onClick={onClose} disabled={loading}>
            Cancel
          </button>
          <button
            className="assign-btn"
            onClick={handleAssign}
            disabled={loading || !personality || success}
          >
            {loading ? 'Assigning...' : success ? 'Done!' : 'Assign Avatar'}
          </button>
        </div>
      </div>
    </div>
  );
}
