import type { BankPool } from './types';
import { fmt, labelFor } from './ledgerUtils';

// Grouped deposits→pool→draws view so the closed-economy loop is legible
// at a glance. Deposits are destructions (negative in by_reason); draws
// are creations (positive). We show absolute magnitudes per direction.
export function BankPoolFlow({
  pool,
  byReason,
}: {
  pool: BankPool;
  byReason: Record<string, number>;
}) {
  const rows = (reasons: string[]) =>
    reasons
      .map((r) => ({ reason: r, amount: Math.abs(byReason[r] ?? 0) }))
      .filter((d) => d.amount !== 0)
      .sort((a, b) => b.amount - a.amount);

  const deposits = rows(pool.deposit_reasons);
  const draws = rows(pool.draw_reasons);
  const sum = (xs: { amount: number }[]) => xs.reduce((s, x) => s + x.amount, 0);

  const directionTable = (
    items: { reason: string; amount: number }[],
    sign: '+' | '-',
    totalLabel: string
  ) =>
    items.length === 0 ? (
      <p className="chip-ledger-empty">none yet</p>
    ) : (
      <table>
        <tbody>
          {items.map((d) => (
            <tr key={d.reason}>
              <td title={d.reason}>{labelFor(d.reason)}</td>
              <td className={`amount ${sign === '+' ? 'pos' : 'neg'}`}>
                {sign}
                {fmt(d.amount)}
              </td>
            </tr>
          ))}
          <tr className="chip-ledger-pool-flow__subtotal">
            <td>
              <strong>{totalLabel}</strong>
            </td>
            <td className={`amount ${sign === '+' ? 'pos' : 'neg'}`}>
              <strong>
                {sign}
                {fmt(sum(items))}
              </strong>
            </td>
          </tr>
        </tbody>
      </table>
    );

  return (
    <section className="chip-ledger-card chip-ledger-pool-flow">
      <h3>Bank pool flow</h3>
      <h4>Deposits → pool</h4>
      {directionTable(deposits, '+', 'Total in')}
      <h4>Pool → draws</h4>
      {directionTable(draws, '-', 'Total out')}
      <p className="chip-ledger-bank-pool-caveat">
        Reserves = deposits − draws = <strong>{fmt(pool.reserves)}</strong>. Rake + vice feed the
        pool; the side hustle + tourist injection draw it down. A dry pool starves the side hustle
        (broke AIs stay broke until rake/vice refill it).
      </p>
    </section>
  );
}
