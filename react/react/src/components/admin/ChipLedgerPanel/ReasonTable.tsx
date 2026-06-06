import { signed, labelFor } from './ledgerUtils';

export function ReasonTable({ totals }: { totals: Record<string, number> }) {
  const entries = Object.entries(totals);
  if (entries.length === 0) {
    return <p className="chip-ledger-empty">No data in this window.</p>;
  }
  entries.sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  return (
    <table>
      <tbody>
        {entries.map(([reason, amount]) => (
          <tr key={reason}>
            <td title={reason}>{labelFor(reason)}</td>
            <td className={`amount ${amount > 0 ? 'pos' : amount < 0 ? 'neg' : ''}`}>
              {signed(amount)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
