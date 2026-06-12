import { useEffect, useState } from 'react';
import { rawAvailability, suggestChatOnDevice, type ChatSuggestion } from '../../utils/onDeviceLLM';

/**
 * On-device LLM smoke test (PROOF OF CONCEPT). Visit `/dev/fmtest` in the iOS app.
 *
 * Exercises the Apple Foundation Models bridge directly — no flag, no game, no
 * server. `suggestChatOnDevice` has zero network path, so any suggestions shown
 * here are generated on the phone. Run it in Airplane Mode to prove it.
 */
const PROMPT =
  'You are Batman at a poker table. You just bluffed your opponent off a big pot. ' +
  'Write 3 short quick-chat lines, needling tone, under 12 words each.';

export function FMTest() {
  const [avail, setAvail] = useState<string>('checking…');
  const [busy, setBusy] = useState(false);
  const [ms, setMs] = useState<number | null>(null);
  const [suggestions, setSuggestions] = useState<ChatSuggestion[]>([]);
  const [error, setError] = useState<string>('');
  const [flagOn, setFlagOn] = useState(() => localStorage.getItem('onDeviceLLM') !== '0');

  useEffect(() => {
    rawAvailability().then((r) => setAvail(JSON.stringify(r)));
  }, []);

  const toggleFlag = () => {
    const next = !flagOn;
    if (next)
      localStorage.removeItem('onDeviceLLM'); // default = on
    else localStorage.setItem('onDeviceLLM', '0'); // kill switch = server route
    setFlagOn(next);
  };

  const generate = async () => {
    setBusy(true);
    setError('');
    setSuggestions([]);
    setMs(null);
    const t0 = performance.now();
    try {
      const out = await suggestChatOnDevice({ prompt: PROMPT, tones: ['needle', 'bravado'] });
      setSuggestions(out);
    } catch (e) {
      setError(String(e));
    } finally {
      setMs(Math.round(performance.now() - t0));
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        padding: 20,
        fontFamily: 'system-ui',
        color: '#fff',
        background: '#111',
        minHeight: '100vh',
      }}
    >
      <h2>On-device LLM test</h2>

      <p style={{ fontSize: 13, opacity: 0.85 }}>availability:</p>
      <pre
        style={{
          background: '#000',
          padding: 10,
          borderRadius: 8,
          fontSize: 12,
          whiteSpace: 'pre-wrap',
        }}
      >
        {avail}
      </pre>

      <button
        onClick={generate}
        disabled={busy}
        style={{
          marginTop: 12,
          padding: '12px 20px',
          fontSize: 16,
          borderRadius: 10,
          border: 'none',
          background: busy ? '#555' : '#0a84ff',
          color: '#fff',
        }}
      >
        {busy ? 'Generating on-device…' : 'Generate on-device'}
      </button>

      {ms !== null && <p style={{ fontSize: 12, opacity: 0.7 }}>took {ms} ms</p>}

      <hr style={{ borderColor: '#333', margin: '20px 0' }} />
      <p style={{ fontSize: 13, opacity: 0.85 }}>
        In-app quick chats use on-device only when this flag is ON:
      </p>
      <button
        onClick={toggleFlag}
        style={{
          padding: '12px 20px',
          fontSize: 16,
          borderRadius: 10,
          border: 'none',
          background: flagOn ? '#34c759' : '#555',
          color: '#fff',
        }}
      >
        on-device quick chats: {flagOn ? 'ON' : 'OFF'} — tap to toggle
      </button>
      <p style={{ fontSize: 12, opacity: 0.6, marginTop: 8 }}>
        With this ON, go play a hand and open quick chat — suggestions come from the phone.
      </p>

      {error && (
        <pre
          style={{
            background: '#400',
            padding: 10,
            borderRadius: 8,
            fontSize: 12,
            whiteSpace: 'pre-wrap',
          }}
        >
          ERROR: {error}
        </pre>
      )}

      {suggestions.length > 0 && (
        <ul style={{ marginTop: 12, lineHeight: 1.6 }}>
          {suggestions.map((s, i) => (
            <li key={i}>
              <strong>[{s.tone}]</strong> {s.text}
            </li>
          ))}
        </ul>
      )}

      <p style={{ marginTop: 24, fontSize: 12, opacity: 0.6 }}>
        Tip: turn on Airplane Mode, then Generate. If lines still appear, it&apos;s on-device.
      </p>
    </div>
  );
}
