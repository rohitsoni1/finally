import type { PricePoint } from "@/lib/priceStore";

const MAX_POINTS = 120;

export function sparklinePath(points: PricePoint[], width: number, height: number): string {
  const pts = points.slice(-MAX_POINTS);
  if (pts.length < 2) return "";
  let min = Infinity;
  let max = -Infinity;
  for (const p of pts) {
    if (p.value < min) min = p.value;
    if (p.value > max) max = p.value;
  }
  const span = max - min || 1;
  const step = width / (pts.length - 1);
  return pts
    .map((p, i) => {
      const x = i * step;
      const y = height - 1 - ((p.value - min) / span) * (height - 2);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

export function Sparkline({ points, className = "" }: { points: PricePoint[]; className?: string }) {
  const W = 100;
  const H = 24;
  const d = sparklinePath(points, W, H);
  const first = points[Math.max(0, points.length - MAX_POINTS)]?.value;
  const last = points[points.length - 1]?.value;
  const color = first == null || last == null || last === first ? "#7d8a9a" : last > first ? "#2bb673" : "#e5484d";
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className={`h-6 w-full ${className}`}
      aria-hidden="true"
    >
      {d ? (
        <path d={d} fill="none" stroke={color} strokeWidth={1.25} vectorEffect="non-scaling-stroke" />
      ) : (
        <line x1="0" x2={W} y1={H / 2} y2={H / 2} stroke="#2f3b4b" strokeDasharray="2 3" vectorEffect="non-scaling-stroke" />
      )}
    </svg>
  );
}
