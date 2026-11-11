"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Portfolio, Snapshot, WatchlistEntry } from "@/lib/types";

const HISTORY_REFRESH_MS = 30_000;

export function useTerminalData() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [watchlist, setWatchlist] = useState<WatchlistEntry[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const refreshPortfolio = useCallback(async () => {
    try {
      setPortfolio(await api.portfolio());
      setLoadError(null);
    } catch (e) {
      setLoadError((e as Error).message);
    }
  }, []);

  const refreshHistory = useCallback(async () => {
    try {
      setSnapshots(await api.history());
    } catch {
      /* chart keeps previous data */
    }
  }, []);

  const refreshWatchlist = useCallback(async () => {
    try {
      setWatchlist(await api.watchlist());
    } catch (e) {
      setLoadError((e as Error).message);
    }
  }, []);

  const refreshAll = useCallback(
    () => Promise.all([refreshPortfolio(), refreshHistory(), refreshWatchlist()]),
    [refreshPortfolio, refreshHistory, refreshWatchlist],
  );

  useEffect(() => {
    // Initial load + periodic snapshot refresh (backend records every 30s).
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetching on mount is the intent
    void refreshAll();
    const id = setInterval(() => {
      void refreshHistory();
      void refreshPortfolio();
    }, HISTORY_REFRESH_MS);
    return () => clearInterval(id);
  }, [refreshAll, refreshHistory, refreshPortfolio]);

  return {
    portfolio,
    setPortfolio,
    snapshots,
    watchlist,
    loadError,
    refreshPortfolio,
    refreshHistory,
    refreshWatchlist,
    refreshAll,
  };
}
