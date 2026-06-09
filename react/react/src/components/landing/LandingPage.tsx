import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { useNavigate } from 'react-router-dom';
import menuBanner from '../../assets/menu-banner.webp';
import tableShot from '../../assets/screenshots/mobile-table.png';
import chatShot from '../../assets/screenshots/mobile-chat.png';
import lobbyShot from '../../assets/screenshots/mobile-lobby.png';
import dossierShot from '../../assets/screenshots/mobile-dossier.png';
import './LandingPage.css';

/**
 * Reveal-on-scroll: a single IntersectionObserver toggles `is-visible` on every
 * [data-reveal] descendant as it enters the viewport. Respects reduced motion
 * by revealing everything immediately.
 */
function useReveal() {
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    const targets = Array.from(root.querySelectorAll<HTMLElement>('[data-reveal]'));
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    if (reduce || !('IntersectionObserver' in window)) {
      targets.forEach((el) => el.classList.add('is-visible'));
      return;
    }

    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-visible');
            io.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.18, rootMargin: '0px 0px -8% 0px' }
    );

    targets.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);

  return rootRef;
}

/** A single phone mockup wrapping a real in-app screenshot. */
function PhoneShot({ src, alt, className = '' }: { src: string; alt: string; className?: string }) {
  return (
    <figure className={`lp-phone lp-phone--solo ${className}`}>
      <img src={src} alt={alt} />
    </figure>
  );
}

const accent = (color: string) => ({ ['--accent' as string]: color }) as CSSProperties;

/**
 * Real characters from the game's roster — "somebody" anchors the thesis, then
 * the reel rolls through a handful of recognizable opponents.
 */
const REEL_NAMES = [
  'somebody',
  'Napoleon',
  'Cleopatra',
  'Machiavelli',
  'Sun Tzu',
  'Joan of Arc',
  'Socrates',
  'Nikola Tesla',
  'Mark Twain',
  'Confucius',
  'Wyatt Earp',
  'King Tut',
];

/** Slot-machine reel: rolls the word after "Every seat is" through real opponents. */
function HeroReel() {
  const [i, setI] = useState(0);
  const [snap, setSnap] = useState(false);

  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const id = setInterval(() => setI((p) => p + 1), 1700);
    return () => clearInterval(id);
  }, []);

  // Seamless wrap: a duplicate of the first word lives at the end of the track.
  // After rolling onto it, snap back to index 0 with the transition disabled.
  useEffect(() => {
    if (i === REEL_NAMES.length) {
      const t = setTimeout(() => {
        setSnap(true);
        setI(0);
      }, 560);
      return () => clearTimeout(t);
    }
    if (snap) {
      const r = requestAnimationFrame(() => setSnap(false));
      return () => cancelAnimationFrame(r);
    }
  }, [i, snap]);

  const items = [...REEL_NAMES, REEL_NAMES[0]];

  return (
    <span className="lp-reel" aria-hidden="true">
      <span
        className={`lp-reel__track${snap ? ' lp-reel__track--snap' : ''}`}
        style={{ ['--reel-i' as string]: i } as CSSProperties}
      >
        {items.map((name, idx) => (
          <span className="lp-reel__word" key={idx}>
            {name}.
          </span>
        ))}
      </span>
    </span>
  );
}

export function LandingPage() {
  const navigate = useNavigate();
  const rootRef = useReveal();

  const scrollTo = (id: string) => () => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <div className="lp" ref={rootRef}>
      {/* Ambient layers */}
      <div className="lp-bg" aria-hidden="true">
        <span className="lp-bg__glow lp-bg__glow--gold" />
        <span className="lp-bg__glow lp-bg__glow--felt" />
        <span className="lp-bg__suit lp-bg__suit--spade">♠</span>
        <span className="lp-bg__grain" />
      </div>

      {/* ===================== TOP BAR ===================== */}
      <header className="lp-topbar">
        <img src={menuBanner} alt="My Poker Face" className="lp-topbar__logo" />
        <nav className="lp-topbar__nav">
          <button className="lp-link" onClick={scrollTo('players')}>
            The Players
          </button>
          <button className="lp-link" onClick={scrollTo('modes')}>
            Modes
          </button>
          <button className="lp-link" onClick={scrollTo('climb')}>
            The Climb
          </button>
          <button className="lp-btn lp-btn--ghost lp-btn--sm" onClick={() => navigate('/login')}>
            Sign in
          </button>
        </nav>
      </header>

      {/* ===================== HERO ===================== */}
      <section className="lp-hero">
        <div className="lp-hero__copy">
          <p className="lp-kicker" data-reveal>
            <span className="lp-kicker__dot" /> No&nbsp;limit · After&nbsp;hours · AI&nbsp;poker
          </p>
          <h1 className="lp-hero__title" data-reveal>
            You vs.
            <br />
            <HeroReel />
            <span className="lp-visually-hidden">somebody.</span>
          </h1>
          <p className="lp-hero__lead" data-reveal>
            Poker is more than numbers — it&apos;s reads, tells, and drama. You&apos;re up against
            AI personalities that feel the swings, remember how you play, and hold a grudge.
          </p>
          <div className="lp-hero__actions" data-reveal>
            <button className="lp-btn lp-btn--primary" onClick={() => navigate('/login')}>
              Play now — it&apos;s free
            </button>
            <button className="lp-btn lp-btn--ghost" onClick={scrollTo('players')}>
              See how it plays
            </button>
          </div>
        </div>

        <div className="lp-hero__stage" data-reveal>
          <div className="lp-phones">
            <figure className="lp-phone lp-phone--back">
              <img
                src={chatShot}
                alt="In-game quick chat — needle, flatter, or trash-talk your opponents"
              />
            </figure>
            <figure className="lp-phone lp-phone--front">
              <img
                src={tableShot}
                alt="A live poker table against AI opponents Napoleon and Jim Cramer"
              />
            </figure>
          </div>
        </div>
      </section>

      {/* ===================== THE PLAYERS (bento) ===================== */}
      <section className="lp-section" id="players">
        <div className="lp-section__head">
          <p className="lp-eyebrow" data-reveal>
            <span>01</span> Opponents with a pulse
          </p>
          <h2 className="lp-h2" data-reveal>
            The table is full of <em>people</em>.
          </h2>
          <p className="lp-section__lead" data-reveal>
            Most poker AI plays a chart. Ours plays a person — with an ego, a mood, and a memory of
            what you just pulled on them.
          </p>
        </div>

        <div className="lp-bento">
          <article
            className="lp-tile lp-tile--feature"
            style={accent('var(--color-gold)')}
            data-reveal
          >
            <span className="lp-tile__glyph" aria-hidden="true">
              ♠
            </span>
            <div className="lp-tile__body">
              <h3 className="lp-tile__title">Real personalities</h3>
              <p className="lp-tile__text">
                A deep cast of distinct characters, each with their own style, confidence, and
                tells. They size you up across a session and bend their game toward your leaks.
              </p>
            </div>
            <PhoneShot
              src={dossierShot}
              alt="A character dossier — Salvador Dali's play style, attitude, tells, and your history against him"
              className="lp-tile__phone"
            />
          </article>

          <article className="lp-tile" style={accent('var(--color-ruby)')} data-reveal>
            <span className="lp-tile__glyph" aria-hidden="true">
              ♥
            </span>
            <div className="lp-tile__body">
              <h3 className="lp-tile__title">Emotions &amp; tilt</h3>
              <p className="lp-tile__text">
                Bad beats sting. Crack a calm pro&apos;s aces and watch the discipline crack with
                them — then punish the spew.
              </p>
            </div>
          </article>

          <article className="lp-tile" style={accent('var(--color-amethyst)')} data-reveal>
            <span className="lp-tile__glyph" aria-hidden="true">
              ♦
            </span>
            <div className="lp-tile__body">
              <h3 className="lp-tile__title">Rivalries &amp; memory</h3>
              <p className="lp-tile__text">
                They remember your bluffs and form opinions of each other. Respect and heat build
                across the night and reshape how the table treats you.
              </p>
            </div>
          </article>

          <article className="lp-tile" style={accent('var(--color-emerald)')} data-reveal>
            <span className="lp-tile__glyph" aria-hidden="true">
              ♣
            </span>
            <div className="lp-tile__body">
              <h3 className="lp-tile__title">Table talk</h3>
              <p className="lp-tile__text">
                Needle them into calling, get under their skin, or just trade banter. What you say
                lands differently on every personality.
              </p>
            </div>
          </article>

          <article className="lp-tile" style={accent('var(--color-sapphire)')} data-reveal>
            <span className="lp-tile__glyph lp-tile__glyph--target" aria-hidden="true">
              ◎
            </span>
            <div className="lp-tile__body">
              <h3 className="lp-tile__title">Read &amp; exploit</h3>
              <p className="lp-tile__text">
                Every opponent has strengths — and a leak or two. Watch how they play, uncover the
                crack, and turn their own pattern against them. Figuring out who you&apos;re really
                up against is the whole game.
              </p>
            </div>
          </article>
        </div>
      </section>

      {/* ===================== MODES (triptych) ===================== */}
      <section className="lp-section lp-section--modes" id="modes">
        <div className="lp-section__head lp-section__head--center">
          <p className="lp-eyebrow" data-reveal>
            <span>02</span> Three ways to play
          </p>
          <h2 className="lp-h2" data-reveal>
            Pick your <em>door</em>.
          </h2>
        </div>

        <div className="lp-doors">
          <article className="lp-door" style={accent('var(--color-emerald)')} data-reveal>
            <span className="lp-door__num">01</span>
            <h3 className="lp-door__title">The Circuit</h3>
            <p className="lp-door__text">
              Pick a stake, take a seat, and grind a bankroll up through the rooms — a cash world
              that remembers you between sessions.
            </p>
            <span className="lp-door__rule" />
            <span className="lp-door__tag">Cash · bankroll</span>
          </article>

          <article
            className="lp-door lp-door--raised"
            style={accent('var(--color-gold)')}
            data-reveal
          >
            <span className="lp-door__num">02</span>
            <h3 className="lp-door__title">Tournaments</h3>
            <p className="lp-door__text">
              Single table, winner takes all. Survive the bust-outs, ride the blinds up, and be the
              last one with chips in front of you.
            </p>
            <span className="lp-door__rule" />
            <span className="lp-door__tag">Winner takes all</span>
          </article>

          <article className="lp-door" style={accent('var(--color-sapphire)')} data-reveal>
            <span className="lp-door__num">03</span>
            <h3 className="lp-door__title">Practice</h3>
            <p className="lp-door__text">
              Learn with a coach reading every hand. Dial the difficulty up or down — nothing
              counts, so try the bold line.
            </p>
            <span className="lp-door__rule" />
            <span className="lp-door__tag">Coached · low stakes</span>
          </article>
        </div>
      </section>

      {/* ===================== THE COACH ===================== */}
      <section className="lp-section lp-section--coach" id="coach">
        <div className="lp-coach__intro">
          <p className="lp-eyebrow" data-reveal>
            <span>03</span> Your corner
          </p>
          <h2 className="lp-h2" data-reveal>
            A coach who&apos;s read <em>every hand</em> you&apos;ve played.
          </h2>
          <p className="lp-section__lead" data-reveal>
            Practice mode isn&apos;t just gentler opponents. A coach studies how you actually play,
            names the leaks bleeding your stack, and turns each one into a drill — then tracks
            whether you&apos;re plugging them.
          </p>
          <ul className="lp-coach__list">
            <li data-reveal>
              <b>Learns your game.</b> It reads every decision you make and builds a picture of your
              tendencies.
            </li>
            <li data-reveal>
              <b>Names your leaks.</b> Calling too wide? Folding the river too often? It shows you
              where the chips go — with the hands to prove it.
            </li>
            <li data-reveal>
              <b>Coaches from real hands.</b> Advice grounded in pots you actually played, not
              generic chart talk.
            </li>
            <li data-reveal>
              <b>Drills &amp; tracks progress.</b> Turns each leak into a focused drill and charts
              your improvement hand after hand.
            </li>
          </ul>
        </div>

        <aside className="lp-dossier" data-reveal>
          <div className="lp-dossier__head">
            <span className="lp-dossier__tag">Coaching dossier</span>
            <span className="lp-dossier__dot" />
          </div>

          <div className="lp-dossier__block">
            <div className="lp-dossier__row">
              <span className="lp-dossier__k">Top leak</span>
              <span className="lp-dossier__v">Over-folding the river</span>
            </div>
            <div className="lp-meter">
              <i style={{ width: '72%' }} />
            </div>
          </div>

          <div className="lp-dossier__block">
            <div className="lp-dossier__row">
              <span className="lp-dossier__k">Active drill</span>
              <span className="lp-dossier__v">River defense</span>
            </div>
            <div className="lp-dossier__row lp-dossier__row--muted">
              <span>12 / 20 spots cleared</span>
              <span>this week</span>
            </div>
          </div>

          <div className="lp-dossier__block">
            <div className="lp-dossier__row">
              <span className="lp-dossier__k">Improvement</span>
              <span className="lp-dossier__up">↑ 18%</span>
            </div>
            <svg
              className="lp-spark"
              viewBox="0 0 120 32"
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              <polyline points="0,28 20,26 40,27 60,20 80,16 100,9 120,5" />
            </svg>
          </div>
        </aside>
      </section>

      {/* ===================== THE CLIMB (staircase) ===================== */}
      <section className="lp-section lp-section--climb" id="climb">
        <div className="lp-climb__intro">
          <p className="lp-eyebrow" data-reveal>
            <span>04</span> The long game
          </p>
          <h2 className="lp-h2" data-reveal>
            Start a nobody.
            <br />
            <em>Become a fixture.</em>
          </h2>
          <p className="lp-section__lead" data-reveal>
            The Circuit isn&apos;t a lobby of price tags — it&apos;s a world you earn your way into.
            Most doors start invisible. You open them by being someone worth knowing.
          </p>
          <PhoneShot
            src={lobbyShot}
            alt="The Circuit lobby — your bankroll, reputation standing, and live table activity"
            className="lp-climb__phone"
          />
        </div>

        <ol className="lp-stairs">
          <li className="lp-step" data-reveal>
            <span className="lp-step__num">01</span>
            <div className="lp-step__body">
              <h3 className="lp-step__title">Get staked</h3>
              <p className="lp-step__text">
                An old grinder takes you under his wing, fronts your first buy-in, and teaches you
                to read the table before you risk a chip.
              </p>
            </div>
          </li>
          <li className="lp-step" data-reveal>
            <span className="lp-step__num">02</span>
            <div className="lp-step__body">
              <h3 className="lp-step__title">Earn the vouch</h3>
              <p className="lp-step__text">
                Play well and play right, and the regulars vouch you into rooms you couldn&apos;t
                even see before. Respect is the key; the bankroll is just the cover charge.
              </p>
            </div>
          </li>
          <li className="lp-step" data-reveal>
            <span className="lp-step__num">03</span>
            <div className="lp-step__body">
              <h3 className="lp-step__title">Build a name</h3>
              <p className="lp-step__text">
                Climb the stakes and a reputation forms around you. Eventually money stops being the
                point — standing is the thing money can&apos;t buy.
              </p>
            </div>
          </li>
          <li className="lp-step lp-step--crown" data-reveal>
            <span className="lp-step__num">04</span>
            <div className="lp-step__body">
              <h3 className="lp-step__title">Become the room</h3>
              <p className="lp-step__text">
                Back an up-and-comer, coach their leaks, and watch them climb on your name. Stop
                chasing your own score and start building someone else&apos;s.
              </p>
            </div>
          </li>
        </ol>
      </section>

      {/* ===================== FINAL CTA ===================== */}
      <section className="lp-closer">
        <span className="lp-closer__suit lp-closer__suit--a" aria-hidden="true">
          ♦
        </span>
        <span className="lp-closer__suit lp-closer__suit--b" aria-hidden="true">
          ♣
        </span>
        <div className="lp-closer__inner" data-reveal>
          <h2 className="lp-closer__title">
            Pull up a <em>chair</em>.
          </h2>
          <p className="lp-closer__lead">
            Free to play in your browser. Your opponents are already waiting — and they remember
            where you left off.
          </p>
          <button className="lp-btn lp-btn--primary lp-btn--lg" onClick={() => navigate('/login')}>
            Play now
          </button>
        </div>
      </section>

      {/* ===================== FOOTER ===================== */}
      <footer className="lp-footer">
        <span className="lp-footer__mark">
          My Poker Face — AI that plays a person, not a chart.
        </span>
        <span className="lp-footer__links">
          <a href="/privacy.html">Privacy</a>
          <i aria-hidden="true">·</i>
          <a href="/terms.html">Terms</a>
        </span>
      </footer>
    </div>
  );
}
