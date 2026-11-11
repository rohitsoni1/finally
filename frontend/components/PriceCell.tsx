"use client";

import { useEffect, useState } from "react";
import { formatPrice } from "@/lib/format";

export const FLASH_HOLD_MS = 150;

interface PriceCellProps {
  price: number | null | undefined;
  testId?: string;
  className?: string;
}

/**
 * Shows a price and flashes green/red when it changes. The flash class is
 * held briefly, then removed so the CSS transition fades it out (~500ms).
 */
export function PriceCell({ price, testId, className = "" }: PriceCellProps) {
  const [prev, setPrev] = useState(price);
  const [flash, setFlash] = useState<{ dir: "up" | "down"; id: number } | null>(null);

  // Derive the flash from a prop change during render (no effect round-trip).
  if (price !== prev) {
    setPrev(price);
    if (price != null && prev != null && price !== prev) {
      setFlash({ dir: price > prev ? "up" : "down", id: (flash?.id ?? 0) + 1 });
    }
  }

  useEffect(() => {
    if (!flash) return;
    const t = setTimeout(() => setFlash(null), FLASH_HOLD_MS);
    return () => clearTimeout(t);
  }, [flash]);

  return (
    <span
      data-testid={testId}
      data-flash={flash?.dir ?? undefined}
      className={`price-cell num inline-block px-1 ${flash ? `flash-${flash.dir}` : ""} ${className}`}
    >
      {formatPrice(price)}
    </span>
  );
}
