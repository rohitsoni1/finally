"use client";

import { useState, type FormEvent } from "react";
import { normalizeTicker } from "@/lib/api";
import { formatPrice, formatQty, formatUsd } from "@/lib/format";
import type { Side, TradeResponse } from "@/lib/types";

interface TradeBarProps {
  selected: string | null;
  lastPrice: number | null;
  cash: number | null;
  onTrade: (ticker: string, quantity: number, side: Side) => Promise<TradeResponse>;
}

/** Market order entry. Instant fill, no confirmation. */
export function TradeBar({ selected, lastPrice, cash, onTrade }: TradeBarProps) {
  const [ticker, setTicker] = useState(selected ?? "");
  const [syncedSel, setSyncedSel] = useState(selected);
  const [qty, setQty] = useState("");
  const [busy, setBusy] = useState<Side | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Selecting a ticker elsewhere pre-fills the order ticker.
  if (selected !== syncedSel) {
    setSyncedSel(selected);
    if (selected) setTicker(selected);
  }

  const submit = async (side: Side) => {
    setNotice(null);
    const t = normalizeTicker(ticker);
    if (!t) {
      setError("Enter a ticker symbol.");
      return;
    }
    const q = Number(qty);
    if (!qty.trim() || !Number.isFinite(q) || q <= 0) {
      setError("Enter a quantity greater than 0.");
      return;
    }
    setBusy(side);
    setError(null);
    try {
      const res = await onTrade(t, q, side);
      const tr = res.trade;
      setNotice(
        `${tr.side === "buy" ? "Bought" : "Sold"} ${formatQty(tr.quantity)} ${tr.ticker} at ${formatUsd(tr.price)}`,
      );
      setQty("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void submit("buy");
  };

  const q = Number(qty);
  const estimate = lastPrice != null && q > 0 && ticker === selected ? q * lastPrice : null;

  return (
    <form
      onSubmit={onSubmit}
      aria-label="Place a market order"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border border-line bg-surface px-3 py-2"
    >
      <span className="text-[12px] font-semibold text-fg">Market order</span>
      <label className="flex items-center gap-1.5 text-[11px] text-muted">
        Ticker
        <input
          data-testid="trade-ticker"
          value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
          maxLength={10}
          placeholder="AAPL"
          className="num w-20 border border-line bg-bg px-2 py-1 text-[12px] uppercase text-fg placeholder:text-faint focus:border-blue focus:outline-none"
        />
      </label>
      <label className="flex items-center gap-1.5 text-[11px] text-muted">
        Qty
        <input
          data-testid="trade-quantity"
          value={qty}
          onChange={(e) => setQty(e.target.value)}
          inputMode="decimal"
          placeholder="0"
          className="num w-20 border border-line bg-bg px-2 py-1 text-right text-[12px] text-fg placeholder:text-faint focus:border-blue focus:outline-none"
        />
      </label>
      <div className="flex gap-1.5">
        <button
          type="submit"
          data-testid="trade-buy"
          disabled={busy !== null}
          className="bg-purple px-4 py-1 text-[12px] font-semibold text-white hover:bg-purple-hi disabled:opacity-60"
        >
          {busy === "buy" ? "Buying…" : "Buy"}
        </button>
        <button
          type="button"
          data-testid="trade-sell"
          disabled={busy !== null}
          onClick={() => void submit("sell")}
          className="border border-purple px-4 py-1 text-[12px] font-semibold text-fg hover:bg-purple/25 disabled:opacity-60"
        >
          {busy === "sell" ? "Selling…" : "Sell"}
        </button>
      </div>
      <span className="num text-[11px] text-muted">
        {estimate != null ? `≈ ${formatUsd(estimate)}` : lastPrice != null && ticker === selected ? `@ ${formatPrice(lastPrice)}` : ""}
      </span>
      <span className="ml-auto flex min-w-0 items-center text-[11px]" aria-live="polite">
        {error ? (
          <span data-testid="trade-error" role="alert" className="truncate text-down">
            {error}
          </span>
        ) : notice ? (
          <span data-testid="trade-status" className="truncate text-up">
            {notice}
          </span>
        ) : cash != null ? (
          <span className="text-muted">
            Buying power <span className="num text-fg">{formatUsd(cash)}</span>
          </span>
        ) : null}
      </span>
    </form>
  );
}
