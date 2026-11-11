import { formatPercent, formatSignedUsd, formatUsd, trendClass } from "@/lib/format";
import type { ConnectionStatus } from "@/lib/types";

const STATUS: Record<ConnectionStatus, { label: string; dot: string }> = {
  connected: { label: "Live", dot: "bg-up" },
  reconnecting: { label: "Reconnecting", dot: "bg-accent dot-pulse" },
  disconnected: { label: "Disconnected", dot: "bg-down" },
};

interface HeaderProps {
  totalValue: number | null;
  cash: number | null;
  pnl: number | null;
  pnlPercent: number | null;
  positionsValue: number | null;
  status: ConnectionStatus;
}

export function Header({ totalValue, cash, pnl, pnlPercent, positionsValue, status }: HeaderProps) {
  const s = STATUS[status];
  return (
    <header className="flex shrink-0 flex-wrap items-center gap-x-8 gap-y-2 border-b border-line bg-surface px-4 py-2">
      <div className="flex items-center gap-2">
        <svg viewBox="0 0 20 20" className="h-5 w-5" aria-hidden="true">
          <rect x="1" y="11" width="4" height="8" fill="#209dd7" />
          <rect x="8" y="6" width="4" height="13" fill="#753991" />
          <rect x="15" y="1" width="4" height="18" fill="#ecad0a" />
        </svg>
        <span className="text-[16px] font-semibold tracking-tight">FinAlly</span>
      </div>

      <div className="flex items-baseline gap-3">
        <span className="text-[11px] text-muted">Portfolio value</span>
        <span data-testid="total-value" className="num text-[24px] font-medium leading-none text-accent">
          {formatUsd(totalValue)}
        </span>
        <span className={`num text-[12px] ${trendClass(pnl)}`}>
          {formatSignedUsd(pnl)} <span className="opacity-80">({formatPercent(pnlPercent)})</span>
        </span>
      </div>

      <dl className="flex items-baseline gap-6 text-[12px]">
        <div className="flex items-baseline gap-2">
          <dt className="text-[11px] text-muted">Cash</dt>
          <dd data-testid="cash-balance" className="num text-fg">
            {formatUsd(cash)}
          </dd>
        </div>
        <div className="flex items-baseline gap-2">
          <dt className="text-[11px] text-muted">Invested</dt>
          <dd className="num text-fg">{formatUsd(positionsValue)}</dd>
        </div>
      </dl>

      <div
        data-testid="connection-status"
        data-status={status}
        role="status"
        aria-label={`Market data: ${s.label}`}
        className="ml-auto flex items-center gap-2 text-[11px] text-muted"
      >
        <span className={`h-2 w-2 rounded-full ${s.dot}`} />
        {s.label}
      </div>
    </header>
  );
}
