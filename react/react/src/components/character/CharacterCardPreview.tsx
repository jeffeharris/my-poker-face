/**
 * CharacterCardPreview — drop-in route for visually evaluating the
 * dossier card. Mount it at any path:
 *
 *   <Route path="/preview/dossier" element={<CharacterCardPreview />} />
 *
 * Three sample subjects (preflop villain, lobby AI, busted regular)
 * exercise the optional sections so the card's silent-drop behavior
 * is visible.
 */

import { useState } from 'react';
import { CharacterDetailCard, type CharacterDossierData } from './CharacterDetailCard';
import { useDisplayNickname } from '../../stores/nicknameOverridesStore';

const SAMPLES: CharacterDossierData[] = [
  {
    name: 'Bruce Wayne',
    nickname: 'The Caped Crusader',
    emotion: 'focused',
    playStyle: 'Tight-Aggressive',
    attitude: 'calculating, never tilts',
    confidence: 'high — believes preparation beats luck',
    observed: {
      handsObserved: 87,
      vpip: 0.21,
      pfr: 0.18,
      aggressionFactor: 3.4,
    },
    chips: {
      atTable: 12_400,
      bankroll: 84_500,
    },
    affiliation: {
      sponsor: 'Lucius Fox',
      relationship: 'rival',
      relationshipNote: "lost a $40k pot to him in March; hasn't forgotten",
    },
    remark: 'I don’t bluff. I just let the other players believe whatever they need to.',
  },
  {
    name: 'Dolly Parton',
    nickname: 'Backwoods Barbie',
    emotion: 'chatty',
    playStyle: 'Loose-Passive',
    attitude: 'warm, disarming, deceptively soft',
    confidence: 'comfortable — laughs through bad beats',
    chips: {
      atTable: 3_200,
    },
    affiliation: {
      relationship: 'admirer',
      relationshipNote: 'thinks you’re "real cute"; will still take your stack',
    },
    remark: 'Sugar, I’d feel terrible takin’ your money… but I will.',
  },
  {
    name: 'Gordon Ramsay',
    playStyle: 'Maniac',
    attitude: 'volatile — punishes any sign of weakness',
    remark: 'This donkey 3-bet me with seven-deuce off. SEVEN. DEUCE. OFF.',
  },
];

export function CharacterCardPreview() {
  const [openIdx, setOpenIdx] = useState<number | null>(null);
  const [origin, setOrigin] = useState<{ x: number; y: number } | undefined>();
  const displayNickname = useDisplayNickname();

  return (
    <div
      style={{
        minHeight: '100vh',
        background: 'radial-gradient(ellipse at top, #1a3a2a 0%, #0c1a14 70%), #050a08',
        padding: '64px 24px',
        display: 'grid',
        placeItems: 'center',
        fontFamily: 'system-ui, sans-serif',
      }}
    >
      <div style={{ maxWidth: 720, width: '100%', textAlign: 'center', color: '#e8d8b0' }}>
        <h1
          style={{
            fontFamily: '"Bodoni Moda Variable", serif',
            fontWeight: 900,
            fontSize: 'clamp(36px, 6vw, 64px)',
            letterSpacing: '-0.02em',
            marginBottom: 8,
          }}
        >
          Dossier Preview
        </h1>
        <p
          style={{
            fontFamily: '"JetBrains Mono Variable", monospace',
            fontSize: 12,
            letterSpacing: '0.32em',
            color: '#b08433',
            textTransform: 'uppercase',
            marginBottom: 40,
          }}
        >
          tap a subject to open their file
        </p>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
            gap: 16,
          }}
        >
          {SAMPLES.map((s, i) => (
            <button
              key={s.name}
              type="button"
              onClick={(e) => {
                const rect = (e.currentTarget as HTMLButtonElement).getBoundingClientRect();
                setOrigin({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
                setOpenIdx(i);
              }}
              style={{
                padding: '20px 16px',
                background: 'rgba(236, 225, 200, 0.06)',
                border: '1px solid rgba(176, 132, 51, 0.5)',
                color: '#ece1c8',
                fontFamily: '"Fraunces Variable", serif',
                fontSize: 18,
                cursor: 'pointer',
                transition: 'background 0.2s ease',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLButtonElement).style.background =
                  'rgba(236, 225, 200, 0.12)';
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLButtonElement).style.background =
                  'rgba(236, 225, 200, 0.06)';
              }}
            >
              <div style={{ fontWeight: 700 }}>{s.name}</div>
              {(() => {
                const alias = displayNickname({ name: s.name, nickname: s.nickname });
                // Only render the alias row when it actually adds info
                // beyond the name itself (skip when fallback collapses
                // to the name).
                if (alias === s.name) return null;
                return (
                  <div
                    style={{
                      fontSize: 13,
                      fontStyle: 'italic',
                      color: '#b08433',
                      marginTop: 4,
                    }}
                  >
                    &ldquo;{alias}&rdquo;
                  </div>
                );
              })()}
            </button>
          ))}
        </div>
      </div>

      {openIdx !== null && (
        <CharacterDetailCard
          isOpen
          onClose={() => setOpenIdx(null)}
          character={SAMPLES[openIdx]!}
          origin={origin}
        />
      )}
    </div>
  );
}
