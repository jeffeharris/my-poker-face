import type { ReactNode } from 'react';

/**
 * Render chat text with inline *action* segments italicised.
 *
 * The dramatic_sequence convention: text wrapped in *asterisks* is a physical
 * action / aside (shown in italics), everything else is plain speech. This is the
 * INLINE variant — it formats `*...*` wherever it appears within a line, so a
 * fish's "I like kings *blub* you in?" reads as an action mid-sentence rather than
 * showing literal asterisks. (FloatingChat's beat parser only handles whole-line
 * actions; this complements it for inline ones and powers the static SalFloater
 * bubble.)
 *
 * An unmatched trailing `*` (e.g. while a line is still typing out) is left as
 * plain text and resolves once its closing `*` arrives.
 */
export function renderInlineActions(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*([^*]+)\*/g;
  let last = 0;
  let key = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(
      <em key={key++} className="chat-action">
        {m[1]}
      </em>
    );
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
