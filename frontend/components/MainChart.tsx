"use client";

import { useEffect, useRef } from "react";
import { AreaSeries, createChart, type IChartApi, type ISeriesApi } from "lightweight-charts";
import { baseChartOptions, toSecondSeries } from "@/lib/chartTheme";
import type { PricePoint } from "@/lib/priceStore";

interface MainChartProps {
  ticker: string | null;
  points: PricePoint[];
}

/** Canvas area chart of the selected ticker's streamed prices. */
export default function MainChart({ ticker, points }: MainChartProps) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Area"> | null>(null);
  const shown = useRef<{ ticker: string | null; len: number }>({ ticker: null, len: 0 });

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, baseChartOptions());
    series.current = c.addSeries(AreaSeries, {
      lineColor: "#209dd7",
      lineWidth: 2,
      topColor: "rgba(32, 157, 215, 0.26)",
      bottomColor: "rgba(32, 157, 215, 0.0)",
      priceLineColor: "#ecad0a",
      priceLineStyle: 2,
      crosshairMarkerBorderColor: "#0d1117",
      crosshairMarkerBackgroundColor: "#209dd7",
    });
    chart.current = c;
    return () => {
      c.remove();
      chart.current = null;
      series.current = null;
      shown.current = { ticker: null, len: 0 };
    };
  }, []);

  useEffect(() => {
    const s = series.current;
    if (!s) return;
    const data = toSecondSeries(points);
    const prev = shown.current;
    if (prev.ticker !== ticker || data.length < prev.len || data.length - prev.len > 1) {
      s.setData(data);
    } else if (data.length > 0) {
      s.update(data[data.length - 1]);
    }
    // History only spans this session, so always show all of it.
    if (data.length !== prev.len || prev.ticker !== ticker) chart.current?.timeScale().fitContent();
    shown.current = { ticker, len: data.length };
  }, [ticker, points]);

  return <div ref={el} className="absolute inset-0" />;
}
