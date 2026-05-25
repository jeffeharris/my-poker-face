import { useState } from 'react';
import { WinnerAnnouncement } from '../game/WinnerAnnouncement';
import { MobileWinnerAnnouncement } from '../mobile/MobileWinnerAnnouncement';
import type { Player } from '../../types/player';
import './WinnerLayoutSandbox.css';

/**
 * Dev-only sandbox to iterate on the winner-screen layout without playing a
 * real hand. Mounts both the desktop and mobile components side-by-side with
 * mock player + winner data covering the common shapes (showdown, no-showdown,
 * split pot). Switch the active scenario from the toolbar at the top.
 */

type ScenarioId = 'showdown_three' | 'showdown_split' | 'no_showdown';

interface Scenario {
  id: ScenarioId;
  label: string;
  sublabel: string;
  players: Player[];
  winnerInfo: Parameters<typeof WinnerAnnouncement>[0]['winnerInfo'];
}

const mkPlayer = (
  name: string,
  emotion: string,
  stack: number,
  overrides: Partial<Player> = {}
): Player => ({
  name,
  stack,
  bet: 0,
  is_folded: false,
  is_all_in: false,
  is_human: false,
  avatar_url: `/api/avatar/${encodeURIComponent(name)}/${emotion}/full`,
  avatar_emotion: emotion,
  ...overrides,
});

// Backend card shape: { rank: 'A', suit: 'Spades' } — see cardFromBackend
const c = (rank: string, suit: 'Spades' | 'Hearts' | 'Diamonds' | 'Clubs') => ({ rank, suit });

const SCENARIOS: Scenario[] = [
  {
    id: 'showdown_three',
    label: 'Showdown',
    sublabel: 'three players',
    players: [
      mkPlayer('Batman', 'smug', 8400),
      mkPlayer('Gordon Ramsay', 'angry', 2200),
      mkPlayer('Eeyore', 'sad', 1400),
    ],
    winnerInfo: {
      winners: ['Batman'],
      showdown: true,
      hand_name: 'Full House, Aces over Kings',
      pot_breakdown: [
        {
          pot_name: 'Main Pot',
          total_amount: 1800,
          hand_name: 'Full House',
          winners: [{ name: 'Batman', amount: 1800 }],
        },
      ],
      community_cards: [
        c('A', 'Hearts'),
        c('K', 'Spades'),
        c('A', 'Diamonds'),
        c('K', 'Clubs'),
        c('4', 'Hearts'),
      ],
      players_showdown: {
        Batman: {
          cards: [c('A', 'Spades'), c('A', 'Clubs')],
          hand_name: 'Full House, Aces over Kings',
          hand_rank: 7,
          hand_score: 9000,
        },
        'Gordon Ramsay': {
          cards: [c('K', 'Hearts'), c('K', 'Diamonds')],
          hand_name: 'Full House, Kings over Aces',
          hand_rank: 7,
          hand_score: 8500,
        },
        Eeyore: {
          cards: [c('Q', 'Clubs'), c('Q', 'Spades')],
          hand_name: 'Two Pair, Aces and Kings',
          hand_rank: 3,
          hand_score: 4200,
          kickers: ['Q'],
        },
      },
    },
  },
  {
    id: 'showdown_split',
    label: 'Split pot',
    sublabel: 'two winners',
    players: [
      mkPlayer('Batman', 'happy', 5000),
      mkPlayer('Gordon Ramsay', 'happy', 5000),
      mkPlayer('Eeyore', 'sad', 800),
    ],
    winnerInfo: {
      winners: ['Batman', 'Gordon Ramsay'],
      showdown: true,
      hand_name: 'Straight, Ten to Ace',
      pot_breakdown: [
        {
          pot_name: 'Main Pot',
          total_amount: 2400,
          hand_name: 'Straight',
          winners: [
            { name: 'Batman', amount: 1200 },
            { name: 'Gordon Ramsay', amount: 1200 },
          ],
        },
      ],
      community_cards: [
        c('10', 'Spades'),
        c('J', 'Diamonds'),
        c('Q', 'Hearts'),
        c('K', 'Clubs'),
        c('3', 'Spades'),
      ],
      players_showdown: {
        Batman: {
          cards: [c('A', 'Hearts'), c('5', 'Diamonds')],
          hand_name: 'Straight, Ten to Ace',
          hand_rank: 5,
          hand_score: 6000,
        },
        'Gordon Ramsay': {
          cards: [c('A', 'Spades'), c('7', 'Clubs')],
          hand_name: 'Straight, Ten to Ace',
          hand_rank: 5,
          hand_score: 6000,
        },
        Eeyore: {
          cards: [c('9', 'Hearts'), c('9', 'Spades')],
          hand_name: 'Pair of Nines',
          hand_rank: 2,
          hand_score: 1800,
        },
      },
    },
  },
  {
    id: 'no_showdown',
    label: 'No showdown',
    sublabel: 'all opponents folded',
    players: [mkPlayer('Batman', 'smug', 9200), mkPlayer('Gordon Ramsay', 'angry', 1800)],
    winnerInfo: {
      winners: ['Batman'],
      showdown: false,
      hand_name: '',
      pot_breakdown: [
        {
          pot_name: 'Main Pot',
          total_amount: 600,
          hand_name: '',
          winners: [{ name: 'Batman', amount: 600 }],
        },
      ],
    },
  },
];

const DESKTOP_DIMS = '1440 × 900';
const MOBILE_DIMS = '390 × 844';

export function WinnerLayoutSandbox() {
  const [scenarioId, setScenarioId] = useState<ScenarioId>('showdown_three');
  const [showMobile, setShowMobile] = useState(true);
  const [showDesktop, setShowDesktop] = useState(true);

  const scenario = SCENARIOS.find((s) => s.id === scenarioId)!;

  const noop = () => {};
  const noopSend = () => {};

  return (
    <div className="ws">
      <header className="ws-header">
        <div className="ws-header-block">
          <div className="ws-eyebrow">
            <span className="ws-eyebrow-dot" />
            <span>internal · layout sandbox</span>
          </div>
          <h1 className="ws-title">
            Winner card<span className="ws-title-em"> &mdash; preview</span>
          </h1>
          <div className="ws-meta">
            <span>/dev/winner-layout</span>
            <span className="ws-meta-sep">·</span>
            <span>{SCENARIOS.length} scenarios</span>
            <span className="ws-meta-sep">·</span>
            <span>animations frozen</span>
          </div>
        </div>

        <div className="ws-toolbar">
          <div className="ws-toolbar-group" role="radiogroup" aria-label="Scenario">
            <div className="ws-toolbar-label">Scenario</div>
            <div className="ws-segmented">
              {SCENARIOS.map((s, i) => (
                <button
                  key={s.id}
                  type="button"
                  role="radio"
                  aria-checked={scenarioId === s.id}
                  className={`ws-segmented-item${scenarioId === s.id ? ' is-active' : ''}`}
                  onClick={() => setScenarioId(s.id)}
                >
                  <span className="ws-segmented-index">{String(i + 1).padStart(2, '0')}</span>
                  <span className="ws-segmented-label">{s.label}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="ws-toolbar-group">
            <div className="ws-toolbar-label">View</div>
            <div className="ws-pills">
              <label className={`ws-pill${showDesktop ? ' is-on' : ''}`}>
                <input
                  type="checkbox"
                  checked={showDesktop}
                  onChange={(e) => setShowDesktop(e.target.checked)}
                />
                <span>Desktop</span>
              </label>
              <label className={`ws-pill${showMobile ? ' is-on' : ''}`}>
                <input
                  type="checkbox"
                  checked={showMobile}
                  onChange={(e) => setShowMobile(e.target.checked)}
                />
                <span>Mobile</span>
              </label>
            </div>
          </div>
        </div>
      </header>

      <main className="ws-stage">
        {showDesktop && (
          <section className="ws-pane">
            <div className="ws-spec">
              <div className="ws-spec-row">
                <span className="ws-spec-key">Surface</span>
                <span className="ws-spec-val">Desktop</span>
              </div>
              <div className="ws-spec-row">
                <span className="ws-spec-key">Viewport</span>
                <span className="ws-spec-val">{DESKTOP_DIMS}</span>
              </div>
              <div className="ws-spec-row">
                <span className="ws-spec-key">Scenario</span>
                <span className="ws-spec-val">
                  {scenario.label}
                  <span className="ws-spec-sub"> / {scenario.sublabel}</span>
                </span>
              </div>
            </div>
            <div className="ws-frame ws-frame--desktop">
              <div className="ws-frame-chrome">
                <span className="ws-chrome-dot" />
                <span className="ws-chrome-dot" />
                <span className="ws-chrome-dot" />
                <span className="ws-chrome-url">mypokerfacegame.com</span>
              </div>
              <div className="ws-frame-stage">
                <WinnerAnnouncement
                  winnerInfo={{ ...scenario.winnerInfo, is_final_hand: true }}
                  onComplete={noop}
                  players={scenario.players}
                />
              </div>
            </div>
          </section>
        )}

        {showMobile && (
          <section className="ws-pane">
            <div className="ws-spec">
              <div className="ws-spec-row">
                <span className="ws-spec-key">Surface</span>
                <span className="ws-spec-val">Mobile</span>
              </div>
              <div className="ws-spec-row">
                <span className="ws-spec-key">Viewport</span>
                <span className="ws-spec-val">{MOBILE_DIMS}</span>
              </div>
              <div className="ws-spec-row">
                <span className="ws-spec-key">Scenario</span>
                <span className="ws-spec-val">
                  {scenario.label}
                  <span className="ws-spec-sub"> / {scenario.sublabel}</span>
                </span>
              </div>
            </div>
            <div className="ws-frame ws-frame--mobile">
              <div className="ws-frame-notch" />
              <div className="ws-frame-stage">
                <MobileWinnerAnnouncement
                  winnerInfo={{ ...scenario.winnerInfo, is_final_hand: true }}
                  onComplete={noop}
                  gameId="sandbox"
                  playerName="Batman"
                  onSendMessage={noopSend}
                  players={scenario.players}
                />
              </div>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
