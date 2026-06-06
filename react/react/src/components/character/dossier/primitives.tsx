/**
 * Small presentational primitives shared across the dossier sections:
 * the hand-drawn tally strip, the dotted-leader data row, and the
 * ruled section header. Extracted from CharacterDetailCard.tsx.
 *
 * These rely on the `.dossier__*` classes defined in CharacterDetailCard.css,
 * which the card imports once (globally) when it mounts.
 */

import { motion } from 'framer-motion';

/** Tally strip: 10 marks, the first `value*10` filled with hand-drawn ticks. */
export function TallyStrip({
  value,
  label,
  readout,
}: {
  value: number;
  label: string;
  readout?: string;
}) {
  const filled = Math.max(0, Math.min(10, Math.round(value * 10)));
  return (
    <div className="dossier__tally-row">
      <div className="dossier__tally-label">{label}</div>
      <div className="dossier__tally-strip" aria-hidden="true">
        {Array.from({ length: 10 }).map((_, i) => (
          <motion.span
            key={i}
            className={`dossier__tick${i < filled ? ' is-filled' : ''}`}
            initial={{ scaleY: 0, opacity: 0 }}
            animate={{ scaleY: 1, opacity: 1 }}
            transition={{
              delay: 0.4 + i * 0.03,
              duration: 0.18,
              ease: [0.2, 0.8, 0.2, 1],
            }}
          />
        ))}
      </div>
      <div className="dossier__tally-readout">{readout ?? `${Math.round(value * 100)}%`}</div>
    </div>
  );
}

export function DataRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="dossier__data-row">
      <span className="dossier__data-label">{label}</span>
      <span className="dossier__data-leader" aria-hidden="true" />
      <span className="dossier__data-value">{value}</span>
    </div>
  );
}

export function SectionRule({ children }: { children: React.ReactNode }) {
  return (
    <div className="dossier__section-rule">
      <span className="dossier__rule-line" />
      <span className="dossier__rule-label">{children}</span>
      <span className="dossier__rule-line" />
    </div>
  );
}
