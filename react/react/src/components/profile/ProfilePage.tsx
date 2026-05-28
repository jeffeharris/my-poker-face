import { useState, useRef, useCallback, type ChangeEvent, type DragEvent } from 'react';
import { config } from '../../config';
import { useAuth } from '../../hooks/useAuth';
import { adminFetch } from '../../utils/api';
import { PageLayout } from '../shared/PageLayout';
import { PageHeader } from '../shared/PageHeader';
import { MenuBar } from '../shared/MenuBar';
import './ProfilePage.css';

const MAX_BIO_LENGTH = 500;

interface ProfilePageProps {
  onBack: () => void;
}

interface AvatarResponse {
  success: boolean;
  avatar_url?: string;
  error?: string;
}

/**
 * ProfilePage — let the human player set a profile avatar and a short
 * self-description ("about me") that the AI opponents can see and riff on.
 *
 * Avatar can be uploaded, pasted as a URL, generated from a text prompt, or
 * created by stylizing an uploaded photo (img2img). On any change we call
 * `checkAuth()` to refresh the cached user so the new avatar shows in the
 * header dropdown and at the table immediately.
 */
export function ProfilePage({ onBack }: ProfilePageProps) {
  const { user, checkAuth } = useAuth();

  const [bio, setBio] = useState(user?.bio ?? '');
  const [savingBio, setSavingBio] = useState(false);
  const [bioSaved, setBioSaved] = useState(false);

  const [prompt, setPrompt] = useState('');
  const [strength, setStrength] = useState(0.6);

  const [busy, setBusy] = useState<null | 'upload' | 'generate' | 'photo' | 'remove'>(null);
  const [error, setError] = useState<string | null>(null);

  const uploadInputRef = useRef<HTMLInputElement>(null);
  const photoInputRef = useRef<HTMLInputElement>(null);

  // avatar_url carries a backend ?v=<updated_at> token that changes on every
  // re-upload, so the browser re-fetches after a change without a manual buster.
  const avatarSrc = user?.avatar_url ? `${config.API_URL}${user.avatar_url}` : null;
  const initial = (user?.name ?? '?').charAt(0).toUpperCase();

  const applyResult = useCallback(
    async (data: AvatarResponse) => {
      if (data.success && data.avatar_url) {
        await checkAuth();
        setError(null);
      } else {
        setError(data.error || 'Something went wrong');
      }
    },
    [checkAuth]
  );

  // --- avatar: upload file / paste URL ---
  const uploadFile = useCallback(
    async (file: File) => {
      setBusy('upload');
      setError(null);
      try {
        const formData = new FormData();
        formData.append('file', file);
        const res = await adminFetch('/api/profile/avatar/upload', {
          method: 'POST',
          body: formData,
        });
        await applyResult(await res.json());
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Upload failed');
      } finally {
        setBusy(null);
      }
    },
    [applyResult]
  );

  const handleUploadChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) uploadFile(file);
    },
    [uploadFile]
  );

  const handleDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      const file = e.dataTransfer.files?.[0];
      if (file && file.type.startsWith('image/')) uploadFile(file);
    },
    [uploadFile]
  );

  // --- avatar: generate from text ---
  const handleGenerate = useCallback(async () => {
    if (!prompt.trim()) {
      setError('Describe how you want your avatar to look.');
      return;
    }
    setBusy('generate');
    setError(null);
    try {
      const res = await adminFetch('/api/profile/avatar/generate', {
        method: 'POST',
        body: JSON.stringify({ prompt: prompt.trim() }),
      });
      await applyResult(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Generation failed');
    } finally {
      setBusy(null);
    }
  }, [prompt, applyResult]);

  // --- avatar: stylize an uploaded photo (img2img) ---
  const stylizePhoto = useCallback(
    async (file: File) => {
      setBusy('photo');
      setError(null);
      try {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('strength', String(strength));
        if (prompt.trim()) formData.append('prompt', prompt.trim());
        const res = await adminFetch('/api/profile/avatar/generate-photo', {
          method: 'POST',
          body: formData,
        });
        await applyResult(await res.json());
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Generation failed');
      } finally {
        setBusy(null);
      }
    },
    [strength, prompt, applyResult]
  );

  const handlePhotoChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) stylizePhoto(file);
    },
    [stylizePhoto]
  );

  // --- avatar: remove ---
  const handleRemove = useCallback(async () => {
    setBusy('remove');
    setError(null);
    try {
      await adminFetch('/api/profile/avatar', { method: 'DELETE' });
      await checkAuth();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove avatar');
    } finally {
      setBusy(null);
    }
  }, [checkAuth]);

  // --- bio ---
  const handleSaveBio = useCallback(async () => {
    setSavingBio(true);
    setBioSaved(false);
    try {
      const res = await adminFetch('/api/profile/bio', {
        method: 'PUT',
        body: JSON.stringify({ bio }),
      });
      const data = await res.json();
      if (data.success) {
        setBio(data.bio ?? '');
        setBioSaved(true);
        await checkAuth();
      } else {
        setError(data.error || 'Could not save bio');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save bio');
    } finally {
      setSavingBio(false);
    }
  }, [bio, checkAuth]);

  const anyBusy = busy !== null;

  return (
    <>
      <MenuBar onBack={onBack} title="Profile" showUserInfo onMainMenu={onBack} />
      <PageLayout variant="top" glowColor="sapphire" hasMenuBar>
        <PageHeader
          title="Your Profile"
          subtitle="Set an avatar and tell the table about yourself"
        />

        <div className="profile-page">
          {/* Current avatar */}
          <section className="profile-section profile-avatar-current">
            <div className="profile-avatar-preview">
              {avatarSrc ? (
                <img src={avatarSrc} alt="Your avatar" />
              ) : (
                <span className="profile-avatar-initial">{initial}</span>
              )}
            </div>
            {user?.avatar_url && (
              <button
                className="profile-btn profile-btn--ghost"
                onClick={handleRemove}
                disabled={anyBusy}
              >
                {busy === 'remove' ? 'Removing…' : 'Remove avatar'}
              </button>
            )}
          </section>

          {error && <div className="profile-error">{error}</div>}

          {/* Upload / URL */}
          <section className="profile-section">
            <h3>Upload an image</h3>
            <div
              className={`profile-dropzone ${busy === 'upload' ? 'is-busy' : ''}`}
              onDragOver={(e) => e.preventDefault()}
              onDrop={anyBusy ? undefined : handleDrop}
              onClick={() => !anyBusy && uploadInputRef.current?.click()}
            >
              <input
                ref={uploadInputRef}
                type="file"
                accept="image/*"
                onChange={handleUploadChange}
                disabled={anyBusy}
                hidden
              />
              {busy === 'upload' ? 'Uploading…' : 'Drop an image here, or click to choose a file'}
            </div>
          </section>

          {/* Generate from text */}
          <section className="profile-section">
            <h3>Generate from a description</h3>
            <p className="profile-hint">
              Describe how you want to look at the table (e.g. "a grizzled cowboy in a leather
              hat").
            </p>
            <textarea
              className="profile-textarea"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Describe your avatar…"
              rows={2}
              disabled={anyBusy}
            />
            <div className="profile-row">
              <button
                className="profile-btn"
                onClick={handleGenerate}
                disabled={anyBusy || !prompt.trim()}
              >
                {busy === 'generate' ? 'Generating…' : 'Generate avatar'}
              </button>
            </div>
          </section>

          {/* Stylize a photo */}
          <section className="profile-section">
            <h3>Turn a photo into an avatar</h3>
            <p className="profile-hint">
              Upload a photo and we'll stylize it. The description above (if any) guides the style.
            </p>
            <label className="profile-strength">
              Keep close to photo
              <input
                type="range"
                min={0.3}
                max={0.9}
                step={0.05}
                value={strength}
                onChange={(e) => setStrength(Number(e.target.value))}
                disabled={anyBusy}
              />
              More creative
            </label>
            <div className="profile-row">
              <button
                className="profile-btn"
                onClick={() => photoInputRef.current?.click()}
                disabled={anyBusy}
              >
                {busy === 'photo' ? 'Stylizing…' : 'Choose a photo'}
              </button>
              <input
                ref={photoInputRef}
                type="file"
                accept="image/*"
                onChange={handlePhotoChange}
                disabled={anyBusy}
                hidden
              />
            </div>
          </section>

          {/* Bio */}
          <section className="profile-section">
            <h3>About you</h3>
            <p className="profile-hint">
              The AI players can read this — they may compliment, tease, or trash-talk you about it.
            </p>
            <textarea
              className="profile-textarea"
              value={bio}
              maxLength={MAX_BIO_LENGTH}
              onChange={(e) => {
                setBio(e.target.value);
                setBioSaved(false);
              }}
              placeholder="Tell the table something about yourself…"
              rows={3}
            />
            <div className="profile-row profile-row--between">
              <span className="profile-charcount">
                {bio.length}/{MAX_BIO_LENGTH}
              </span>
              <div className="profile-row">
                {bioSaved && <span className="profile-saved">Saved</span>}
                <button className="profile-btn" onClick={handleSaveBio} disabled={savingBio}>
                  {savingBio ? 'Saving…' : 'Save bio'}
                </button>
              </div>
            </div>
          </section>
        </div>
      </PageLayout>
    </>
  );
}

export default ProfilePage;
