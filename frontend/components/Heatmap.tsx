"use client";

import { useEffect, useRef, useState } from "react";
import { squarify } from "@/lib/treemap";
import { pnlColor } from "@/lib/portfolio";
import { formatPercent, formatUsd } from "@/lib/format";
import type { Position } from "@/lib/types";

interface HeatmapProps {
  positions: Position[];
  selected: string | null;
  onSelect: (ticker: string) => void;
}

/** Treemap of positions: area = portfolio weight, colour = unrealized P&L %. */
export function Heatmap({ positions, selected, onSelect }: HeatmapProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 160, h: 100 });

  useEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) setSize({ w: width, h: height });
    });
    ro.observe(node);
    return () => ro.disconnect();
  }, []);

  const rects = squarify(
    positions.map((p) => ({ value: p.market_value, data: p })),
    size.w,
    size.h,
  );

  return (
    <div ref={ref} data-testid="heatmap" data-count={rects.length} className="absolute inset-0 p-1">
      {rects.length === 0 ? (
        <div className="flex h-full items-center justify-center px-6 text-center text-[12px] text-muted">
          No positions yet. Buy shares from the order bar, or ask FinAlly to.
        </div>
      ) : (
        <div className="relative h-full w-full">
          {rects.map(({ x, y, w, h, data: p }) => {
            const tiny = w < 56 || h < 34;
            return (
              <button
                type="button"
                key={p.ticker}
                data-testid={`heatmap-tile-${p.ticker}`}
                data-pnl={p.unrealized_pnl >= 0 ? "up" : "down"}
                onClick={() => onSelect(p.ticker)}
                title={`${p.ticker} · ${formatUsd(p.market_value)} · ${formatPercent(p.unrealized_pnl_percent)}`}
                style={{
                  left: `${(x / size.w) * 100}%`,
                  top: `${(y / size.h) * 100}%`,
                  width: `${(w / size.w) * 100}%`,
                  height: `${(h / size.h) * 100}%`,
                  backgroundColor: pnlColor(p.unrealized_pnl_percent),
                }}
                className={`absolute flex flex-col items-start justify-between overflow-hidden border-2 p-1.5 text-left transition-[background-color] duration-500 ${
                  selected === p.ticker ? "border-accent" : "border-bg"
                }`}
              >
                <span className="text-[12px] font-semibold leading-none text-white">{p.ticker}</span>
                {!tiny ? (
                  <span className="num text-[11px] leading-none text-white/85">
                    {formatPercent(p.unrealized_pnl_percent)}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
