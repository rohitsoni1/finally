"use client";

import { useState, type FormEvent } from "react";
import { Panel } from "./Panel";
import { PriceCell } from "./PriceCell";
import { Sparkline } from "./Sparkline";
import { formatPercent, trendClass } from "@/lib/format";
import { normalizeTicker } from "@/lib/api";
import type { PricePoint } from "@/lib/priceStore";
import type { PriceMap, PriceUpdate, WatchlistEntry } from "@/lib/types";

export function dayChangePercent(p: PriceUpdate | null | undefined): number | null {
  if (!p) return null;
  if (typeof p.day_change_percent === "number") return p.day_change_percent;
  if (p.reference_price) return ((p.price - p.reference_price) / p.reference_price) * 100;
  return null;
}

interface WatchlistProps {
  entries: WatchlistEntry[];
  prices: PriceMap;
  history: Record<string, PricePoint[]>;
  selected: string | null;
  onSelect: (ticker: string) => void;
  onAdd: (ticker: string) => Promise<void>;
  onRemove: (ticker: string) => Promise<void>;
}

export function Watchlist({ entries, prices, history, selected, onSelect, onAdd, onRemove }: WatchlistProps) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const t = normalizeTicker(draft);
    if (!t) {
      setError("Enter a ticker symbol, e.g. PYPL.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onAdd(t);
      setDraft("");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title="Watchlist"
      right={<span className="num">{entries.length}</span>}
      className="h-full"
      bodyClassName="flex flex-col"
    >
      <form onSubmit={submit} className="flex shrink-0 gap-1 border-b border-line p-2">
        <input
          data-testid="watchlist-add-input"
          aria-label="Ticker to add"
          value={draft}
          onChange={(e) => setDraft(e.target.value.toUpperCase())}
          placeholder="Add ticker"
          maxLength={10}
          className="num min-w-0 flex-1 border border-line bg-bg px-2 py-1 text-[12px] uppercase text-fg placeholder:normal-case placeholder:text-faint focus:border-blue focus:outline-none"
        />
        <button
          type="submit"
          data-testid="watchlist-add-button"
          disabled={busy}
          className="border border-line-strong px-2.5 text-[12px] text-fg hover:border-blue hover:text-blue disabled:opacity-50"
        >
          Add
        </button>
      </form>
      {error ? (
        <p role="alert" className="shrink-0 border-b border-line px-2 py-1 text-[11px] text-down">
          {error}
        </p>
      ) : null}

      <ul data-testid="watchlist" className="min-h-0 flex-1 overflow-y-auto">
        {entries.length === 0 ? (
          <li className="px-3 py-6 text-center text-[12px] text-muted">
            Your watchlist is empty. Add a ticker above.
          </li>
        ) : null}
        {entries.map(({ ticker, price: initial }) => {
          const p = prices[ticker] ?? initial ?? null;
          const chg = dayChangePercent(p);
          const isSel = selected === ticker;
          return (
            <li
              key={ticker}
              data-testid={`watchlist-row-${ticker}`}
              aria-current={isSel ? "true" : undefined}
              onClick={() => onSelect(ticker)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(ticker);
                }
              }}
              tabIndex={0}
              className={`group grid cursor-pointer grid-cols-[3.75rem_1fr_4.75rem_3.75rem_1.25rem] items-center gap-2 border-b border-l-2 border-b-line/60 py-1.5 pl-2 pr-1 outline-offset-[-2px] hover:bg-raised ${
                isSel ? "border-l-accent bg-raised" : "border-l-transparent"
              }`}
            >
              <span className={`font-semibold ${isSel ? "text-accent" : "text-fg"}`}>{ticker}</span>
              <Sparkline points={history[ticker] ?? []} />
              <span className="text-right">
                <PriceCell price={p?.price} testId={`price-${ticker}`} />
              </span>
              <span className={`num text-right text-[11px] ${trendClass(chg)}`}>{formatPercent(chg)}</span>
              <button
                type="button"
                data-testid={`watchlist-remove-${ticker}`}
                aria-label={`Remove ${ticker} from watchlist`}
                onClick={(e) => {
                  e.stopPropagation();
                  void onRemove(ticker).catch((err: Error) => setError(err.message));
                }}
                className="flex h-5 w-5 items-center justify-center text-faint opacity-0 hover:text-down focus:opacity-100 group-hover:opacity-100"
              >
                <svg viewBox="0 0 10 10" className="h-2.5 w-2.5" aria-hidden="true">
                  <path d="M1 1l8 8M9 1l-8 8" stroke="currentColor" strokeWidth="1.4" />
                </svg>
              </button>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
