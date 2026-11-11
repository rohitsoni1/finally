"use client";

import { useEffect, useMemo, useSyncExternalStore } from "react";
import { PriceStore, parsePriceEvent, type PriceState } from "@/lib/priceStore";

const STREAM_URL = "/api/stream/prices";
const RETRY_MS = 3000;

/**
 * Opens the SSE price stream once and exposes prices, per-ticker history and
 * connection status. EventSource retries on its own while CONNECTING; if the
 * browser gives up (CLOSED) we open a fresh one after a short delay.
 */
export function usePriceStream(): PriceState {
  const store = useMemo(() => new PriceStore(), []);

  useEffect(() => {
    if (typeof window === "undefined" || typeof EventSource === "undefined") {
      store.setStatus("disconnected");
      return;
    }
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      es = new EventSource(STREAM_URL);
      es.onopen = () => store.setStatus("connected");
      es.onmessage = (ev) => {
        const batch = parsePriceEvent(ev.data);
        if (batch) {
          store.setStatus("connected");
          store.ingest(batch);
        }
      };
      es.onerror = () => {
        if (!es) return;
        if (es.readyState === EventSource.CLOSED) {
          store.setStatus("disconnected");
          es.close();
          es = null;
          retry = setTimeout(connect, RETRY_MS);
        } else {
          store.setStatus("reconnecting");
        }
      };
    };

    connect();
    return () => {
      disposed = true;
      if (retry) clearTimeout(retry);
      es?.close();
    };
  }, [store]);

  return useSyncExternalStore(store.subscribe, store.getState, store.getState);
}
