import { useState, useRef, useCallback, type ChangeEvent, type DragEvent } from 'react';
import { Pencil, ArrowLeft } from 'lucide-react';
import { config } from '../../config';
import { useAuth } from '../../hooks/useAuth';
import { adminFetch } from '../../utils/api';
import '../profile/ProfilePage.css';
import './SettingsPage.css';

const MAX_BIO_LENGTH = 500;

interface AvatarResponse {
  success: boolean;
  avatar_url?: string;
  error?: string;
}

/**
 * ProfileSettings — the Profile section of the Settings page: avatar + bio.
 *
 * Chrome-less (no MenuBar/PageLayout) so SettingsPage can host it alongside
 * other sections. Reuses ProfilePage.css. Extracted from the former standalone
 * ProfilePage when Profile moved under Settings.
 */
export function ProfileSettings() {
  const { user, checkAuth } = useAuth();

  const [bio, setBio] = useState(user?.bio ?? '');
  const [savingBio, setSavingBio] = useState(false);
  const [bioSaved, setBioSaved] = useState(false);

  const [prompt, setPrompt] = useState('');
  const [strength, setStrength] = useState(0.6);

  const [busy, setBusy] = useState<null | 'upload' | 'generate' | 'photo' | 'remove'>(null);
  const [error, setError] = useState<string | null>(null);
  // The avatar-editing tools (upload / generate / photo) live behind this view
  // so the top level stays focused on the avatar + "about me".
  const [editingAvatar, setEditingAvatar] = useState(false);

  const uploadInputRef = useRef<HTMLInputElement>(null);
  const photoInputRef = useRef<HTMLInputElement>(null);

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

  // ─── Avatar edit view: the upload / generate / photo tools, tucked away ───
  if (editingAvatar) {
    return (
      <div className="profile-page">
        <button
          className="settings-subback"
          onClick={() => setEditingAvatar(false)}
          disabled={anyBusy}
        >
          <ArrowLeft size={16} />
          <span>Back to profile</span>
        </button>

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

        <section className="profile-section">
          <h3>Generate from a description</h3>
          <p className="profile-hint">
            Describe how you want to look at the table (e.g. "a grizzled cowboy in a leather hat").
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
      </div>
    );
  }

  // ─── Top level: avatar, then "about me" directly beneath ───
  return (
    <div className="profile-page">
      <section className="profile-section profile-avatar-current">
        <div className="profile-avatar-preview">
          {avatarSrc ? (
            <img src={avatarSrc} alt="Your avatar" />
          ) : (
            <span className="profile-avatar-initial">{initial}</span>
          )}
        </div>
        <button className="profile-btn profile-btn--ghost" onClick={() => setEditingAvatar(true)}>
          <Pencil size={15} />
          {user?.avatar_url ? 'Edit avatar' : 'Add an avatar'}
        </button>
      </section>

      {error && <div className="profile-error">{error}</div>}

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
  );
}

export default ProfileSettings;
