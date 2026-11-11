"use client";

import { PriceCell } from "./PriceCell";
import { formatPercent, formatPrice, formatQty, formatSignedUsd, trendClass } from "@/lib/format";
import type { Position } from "@/lib/types";

interface PositionsTableProps {
  positions: Position[];
  selected: string | null;
  onSelect: (ticker: string) => void;
}

export function PositionsTable({ positions, selected, onSelect }: PositionsTableProps) {
  const sorted = [...positions].sort((a, b) => b.market_value - a.market_value);
  return (
    <div className="absolute inset-0 overflow-auto">
      <table data-testid="positions-table" className="w-full border-collapse text-[12px]">
        <thead className="sticky top-0 bg-surface text-[11px] text-muted">
          <tr className="border-b border-line">
            <th className="px-3 py-1.5 text-left font-medium">Ticker</th>
            <th className="px-2 py-1.5 text-right font-medium">Qty</th>
            <th className="px-2 py-1.5 text-right font-medium">Avg cost</th>
            <th className="px-2 py-1.5 text-right font-medium">Price</th>
            <th className="px-2 py-1.5 text-right font-medium">Unrealized P&amp;L</th>
            <th className="px-3 py-1.5 text-right font-medium">Change</th>
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr>
              <td colSpan={6} className="px-3 py-6 text-center text-muted">
                No open positions. Your $10,000 is waiting in cash.
              </td>
            </tr>
          ) : null}
          {sorted.map((p) => (
            <tr
              key={p.ticker}
              data-testid={`position-row-${p.ticker}`}
              onClick={() => onSelect(p.ticker)}
              className={`cursor-pointer border-b border-line/60 hover:bg-raised ${selected === p.ticker ? "bg-raised" : ""}`}
            >
              <td className={`px-3 py-1.5 font-semibold ${selected === p.ticker ? "text-accent" : ""}`}>{p.ticker}</td>
              <td className="num px-2 py-1.5 text-right" data-testid={`position-qty-${p.ticker}`}>
                {formatQty(p.quantity)}
              </td>
              <td className="num px-2 py-1.5 text-right text-muted">{formatPrice(p.avg_cost)}</td>
              <td className="px-2 py-1.5 text-right">
                <PriceCell price={p.current_price} />
              </td>
              <td className={`num px-2 py-1.5 text-right ${trendClass(p.unrealized_pnl)}`}>
                {formatSignedUsd(p.unrealized_pnl)}
              </td>
              <td className={`num px-3 py-1.5 text-right ${trendClass(p.unrealized_pnl_percent)}`}>
                {formatPercent(p.unrealized_pnl_percent)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
