import { useState } from 'react';

// Interactive island: suggest an AI character + leave an email to be notified
// when it's added to the game. Posts to the Flask backend. We deliberately do
// NOT expose a live AI generator on the marketing site (abuse + cost); this is a
// human-reviewed suggestion queue — adding a vetted persona is a trivial config
// edit on our side.

type Status = 'idle' | 'submitting' | 'done' | 'error';

// Same-origin in production (nginx proxies /api -> Flask). Override for local dev.
const API_BASE = (import.meta.env.PUBLIC_API_BASE as string) || '/api';

export default function RequestCharacter() {
  const [character, setCharacter] = useState('');
  const [email, setEmail] = useState('');
  const [status, setStatus] = useState<Status>('idle');
  const [error, setError] = useState('');

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!character.trim()) return;
    setStatus('submitting');
    setError('');
    try {
      const res = await fetch(`${API_BASE}/character-requests`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          character: character.trim(),
          email: email.trim() || null,
          source: 'opponents-page',
        }),
      });
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      setStatus('done');
    } catch (err) {
      setStatus('error');
      setError(err instanceof Error ? err.message : 'Something went wrong.');
    }
  }

  if (status === 'done') {
    return (
      <div className="reqform reqform--done">
        <p className="reqform__thanks">
          Got it — <strong>{character.trim()}</strong> is on the list.
        </p>
        <p className="reqform__sub">
          {email.trim()
            ? "We'll email you the moment they sit down at the table."
            : 'Add an email next time and we’ll tell you when they’re in the game.'}
        </p>
      </div>
    );
  }

  return (
    <form className="reqform" onSubmit={onSubmit}>
      <div className="reqform__row">
        <label className="reqform__field">
          <span>Who should we add?</span>
          <input
            type="text"
            value={character}
            onChange={(e) => setCharacter(e.target.value)}
            placeholder="e.g. Marie Curie, Tony Soprano, your boss…"
            maxLength={80}
            required
          />
        </label>
        <label className="reqform__field">
          <span>
            Email <em>(optional — to get notified)</em>
          </span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            maxLength={120}
          />
        </label>
      </div>
      <button className="btn btn--gold" type="submit" disabled={status === 'submitting'}>
        {status === 'submitting' ? 'Sending…' : 'Suggest this character'}
      </button>
      {status === 'error' && <p className="reqform__error">{error}</p>}
    </form>
  );
}
