"use client";

import dynamic from "next/dynamic";
import { useCallback, useMemo, useState } from "react";
import { Header } from "./Header";
import { Watchlist, dayChangePercent } from "./Watchlist";
import { Heatmap } from "./Heatmap";
import { PositionsTable } from "./PositionsTable";
import { TradeBar } from "./TradeBar";
import { ChatPanel } from "./ChatPanel";
import { Panel } from "./Panel";
import { PriceCell } from "./PriceCell";
import { usePriceStream } from "@/hooks/usePriceStream";
import { useTerminalData } from "@/hooks/useTerminalData";
import { revaluePortfolio } from "@/lib/portfolio";
import { api } from "@/lib/api";
import { formatPercent, formatPrice, formatUsd, trendClass } from "@/lib/format";
import type { PricePoint } from "@/lib/priceStore";
import type { Side } from "@/lib/types";

const MainChart = dynamic(() => import("./MainChart"), { ssr: false });
const PnlChart = dynamic(() => import("./PnlChart"), { ssr: false });

const STARTING_CASH = 10_000;
const EMPTY: PricePoint[] = [];

export function Terminal() {
  const stream = usePriceStream();
  const data = useTerminalData();
  const [picked, setPicked] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(true);

  const tickers = data.watchlist.map((w) => w.ticker);
  // Default selection: first watchlist ticker until the user picks one.
  const selected = picked ?? tickers[0] ?? null;

  const live = useMemo(
    () => (data.portfolio ? revaluePortfolio(data.portfolio, stream.prices) : null),
    [data.portfolio, stream.prices],
  );

  const { setPortfolio, refreshHistory, refreshAll, refreshWatchlist } = data;

  const onTrade = useCallback(
    async (ticker: string, quantity: number, side: Side) => {
      const res = await api.trade(ticker, quantity, side);
      setPortfolio(res.portfolio);
      void refreshHistory();
      return res;
    },
    [setPortfolio, refreshHistory],
  );

  const onAdd = useCallback(
    async (ticker: string) => {
      await api.addTicker(ticker);
      await refreshWatchlist();
      setPicked(ticker);
    },
    [refreshWatchlist],
  );

  const onRemove = useCallback(
    async (ticker: string) => {
      await api.removeTicker(ticker);
      await refreshWatchlist();
      setPicked((cur) => (cur === ticker ? null : cur));
    },
    [refreshWatchlist],
  );

  const sel = selected ? stream.prices[selected] ?? data.watchlist.find((w) => w.ticker === selected)?.price ?? null : null;
  const selChg = dayChangePercent(sel);
  const selPoints = (selected && stream.history[selected]) || EMPTY;
  const sessionHigh = selPoints.length ? Math.max(...selPoints.map((p) => p.value)) : null;
  const sessionLow = selPoints.length ? Math.min(...selPoints.map((p) => p.value)) : null;
  const baseline = data.snapshots[0]?.total_value ?? STARTING_CASH;

  return (
    <div className="flex h-dvh min-h-[640px] flex-col overflow-hidden max-lg:h-auto max-lg:overflow-visible">
      <Header
        totalValue={live?.total_value ?? null}
        cash={live?.cash_balance ?? null}
        pnl={live?.unrealized_pnl ?? null}
        pnlPercent={live?.unrealized_pnl_percent ?? null}
        positionsValue={live?.positions_value ?? null}
        status={stream.status}
      />
      {data.loadError ? (
        <p role="alert" className="shrink-0 border-b border-down/40 bg-down/10 px-4 py-1 text-[12px] text-down">
          {data.loadError}
        </p>
      ) : null}

      <main
        className={`grid min-h-0 flex-1 gap-1.5 p-1.5 max-lg:grid-cols-1 ${
          chatOpen ? "lg:grid-cols-[minmax(300px,330px)_minmax(0,1fr)_minmax(320px,360px)]" : "lg:grid-cols-[minmax(300px,330px)_minmax(0,1fr)_40px]"
        }`}
      >
        <div className="min-h-0 max-lg:h-[440px]">
          <Watchlist
            entries={data.watchlist}
            prices={stream.prices}
            history={stream.history}
            selected={selected}
            onSelect={setPicked}
            onAdd={onAdd}
            onRemove={onRemove}
          />
        </div>

        <div className="grid min-h-0 min-w-0 grid-rows-[minmax(220px,1.25fr)_auto_minmax(180px,1fr)] gap-1.5 max-lg:grid-rows-[360px_auto_auto]">
          <Panel
            testId="main-chart"
            title={
              <span className="flex items-baseline gap-3">
                <span className="text-[14px] text-accent">{selected ?? "No ticker selected"}</span>
                {sel ? (
                  <>
                    <PriceCell price={sel.price} className="text-[14px]" />
                    <span className={`num text-[12px] font-normal ${trendClass(selChg)}`}>{formatPercent(selChg)} today</span>
                  </>
                ) : null}
              </span>
            }
            right={
              sessionHigh != null ? (
                <span className="num">
                  Session H {formatPrice(sessionHigh)} L {formatPrice(sessionLow)}
                </span>
              ) : (
                <span>Waiting for prices</span>
              )
            }
          >
            <MainChart ticker={selected} points={selPoints} />
          </Panel>

          <TradeBar
            selected={selected}
            lastPrice={sel?.price ?? null}
            cash={live?.cash_balance ?? null}
            onTrade={onTrade}
          />

          <div className="grid min-h-0 grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-1.5 max-xl:grid-cols-1 max-lg:grid-cols-1">
            <div className="grid min-h-0 grid-rows-[minmax(0,1fr)_minmax(0,1fr)] gap-1.5 max-lg:grid-rows-[260px_260px]">
              <Panel
                title="Holdings heatmap"
                right={<span>Size by weight, colour by P&amp;L</span>}
              >
                <Heatmap positions={live?.positions ?? []} selected={selected} onSelect={setPicked} />
              </Panel>
              <Panel
                title="Portfolio value"
                right={<span className="num">{formatUsd(live?.total_value ?? null)}</span>}
              >
                <div data-testid="pnl-chart" data-points={data.snapshots.length} className="absolute inset-0">
                  <PnlChart snapshots={data.snapshots} liveValue={live?.total_value ?? null} baseline={baseline} />
                </div>
              </Panel>
            </div>
            <Panel
              title="Positions"
              right={<span className="num">{live?.positions.length ?? 0} open</span>}
              className="max-lg:h-[320px]"
            >
              <PositionsTable positions={live?.positions ?? []} selected={selected} onSelect={setPicked} />
            </Panel>
          </div>
        </div>

        <div className="min-h-0 max-lg:h-[520px]">
          <ChatPanel open={chatOpen} onToggle={() => setChatOpen((o) => !o)} onActions={() => void refreshAll()} />
        </div>
      </main>
    </div>
  );
}
