"use client";

import { useEffect, useRef } from "react";
import { BaselineSeries, createChart, type IChartApi, type ISeriesApi, type Time } from "lightweight-charts";
import { baseChartOptions, toSecondSeries } from "@/lib/chartTheme";
import type { Snapshot } from "@/lib/types";

interface PnlChartProps {
  snapshots: Snapshot[];
  liveValue: number | null;
  baseline: number;
}

/**
 * Portfolio value over time, split at the starting value: green above,
 * red below. The last point follows the live total between snapshots.
 */
export default function PnlChart({ snapshots, liveValue, baseline }: PnlChartProps) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Baseline"> | null>(null);
  const lastTime = useRef<number>(0);

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      ...baseChartOptions(),
      timeScale: { ...baseChartOptions().timeScale, secondsVisible: false },
    });
    series.current = c.addSeries(BaselineSeries, {
      baseValue: { type: "price", price: baseline },
      topLineColor: "#2bb673",
      topFillColor1: "rgba(43, 182, 115, 0.24)",
      topFillColor2: "rgba(43, 182, 115, 0.02)",
      bottomLineColor: "#e5484d",
      bottomFillColor1: "rgba(229, 72, 77, 0.02)",
      bottomFillColor2: "rgba(229, 72, 77, 0.24)",
      lineWidth: 2,
      priceLineVisible: false,
    });
    chart.current = c;
    return () => {
      c.remove();
      chart.current = null;
      series.current = null;
    };
    // baseline applied separately below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    series.current?.applyOptions({ baseValue: { type: "price", price: baseline } });
  }, [baseline]);

  useEffect(() => {
    const s = series.current;
    if (!s) return;
    const data = toSecondSeries(
      snapshots.map((x) => ({ time: Date.parse(x.recorded_at) / 1000, value: x.total_value })),
    );
    s.setData(data);
    lastTime.current = data.length ? (data[data.length - 1].time as number) : 0;
    chart.current?.timeScale().fitContent();
  }, [snapshots]);

  useEffect(() => {
    const s = series.current;
    if (!s || liveValue == null || lastTime.current === 0) return;
    const now = Math.max(Math.floor(Date.now() / 1000), lastTime.current);
    s.update({ time: now as Time, value: liveValue });
    if (now !== lastTime.current) chart.current?.timeScale().fitContent();
    lastTime.current = now;
  }, [liveValue]);

  return <div ref={el} className="absolute inset-0" />;
}
