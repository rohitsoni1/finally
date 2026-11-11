import { APIRequestContext, Locator, Page, expect } from '@playwright/test';

export const DEFAULT_TICKERS = ['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA', 'NVDA', 'META', 'JPM', 'V', 'NFLX'];

export type Position = {
  ticker: string;
  quantity: number;
  avg_cost: number;
  current_price: number;
  market_value: number;
  cost_basis: number;
  unrealized_pnl: number;
  unrealized_pnl_percent: number;
  weight: number;
};

export type Portfolio = {
  cash_balance: number;
  positions_value: number;
  total_value: number;
  total_cost: number;
  unrealized_pnl: number;
  unrealized_pnl_percent: number;
  positions: Position[];
};

/** Parse "$10,000.00", "10,000", "-$12.34", "(12.34)" etc. into a number. */
export function parseMoney(text: string | null | undefined): number {
  if (!text) return NaN;
  const negative = /^\s*[-−(]|[-−]\s*\$/.test(text);
  const cleaned = text.replace(/[^0-9.]/g, '');
  const n = parseFloat(cleaned);
  return negative ? -n : n;
}

export async function textNumber(locator: Locator): Promise<number> {
  return parseMoney(await locator.innerText());
}

export async function getPortfolio(request: APIRequestContext): Promise<Portfolio> {
  const res = await request.get('/api/portfolio');
  expect(res.status()).toBe(200);
  return res.json();
}

export async function getPosition(request: APIRequestContext, ticker: string): Promise<Position | undefined> {
  const p = await getPortfolio(request);
  return p.positions.find((x) => x.ticker === ticker);
}

export async function getWatchlistTickers(request: APIRequestContext): Promise<string[]> {
  const res = await request.get('/api/watchlist');
  expect(res.status()).toBe(200);
  const body = await res.json();
  return body.tickers.map((t: { ticker: string }) => t.ticker);
}

export async function trade(request: APIRequestContext, ticker: string, side: 'buy' | 'sell', quantity: number) {
  const res = await request.post('/api/portfolio/trade', { data: { ticker, side, quantity } });
  return res;
}

/** Close any existing position in `ticker` so a test starts from a known state. */
export async function flatten(request: APIRequestContext, ticker: string) {
  const pos = await getPosition(request, ticker);
  if (pos && pos.quantity > 0) {
    const res = await trade(request, ticker, 'sell', pos.quantity);
    expect(res.status(), await res.text()).toBe(200);
  }
}

/** Ensure `ticker` is (or is not) on the watchlist via the API. */
export async function ensureWatchlist(request: APIRequestContext, ticker: string, present: boolean) {
  const tickers = await getWatchlistTickers(request);
  if (present && !tickers.includes(ticker)) {
    const res = await request.post('/api/watchlist', { data: { ticker } });
    expect([201, 409]).toContain(res.status());
  } else if (!present && tickers.includes(ticker)) {
    const res = await request.delete(`/api/watchlist/${ticker}`);
    expect([200, 404]).toContain(res.status());
  }
}

/** Wait until the API has a live price for the ticker (simulator has ticked at least once). */
export async function waitForPrice(request: APIRequestContext, ticker: string) {
  await expect
    .poll(
      async () => {
        const res = await request.get('/api/watchlist');
        const body = await res.json();
        const row = body.tickers.find((t: { ticker: string }) => t.ticker === ticker);
        return row?.price?.price ?? null;
      },
      { timeout: 15_000, message: `price for ${ticker} never appeared in /api/watchlist` },
    )
    .toBeGreaterThan(0);
}

/** Load the app and wait for the SSE connection + first prices to land. */
export async function openApp(page: Page) {
  await page.goto('/');
  await expect(page.getByTestId('watchlist')).toBeVisible();
  await expect(page.getByTestId('connection-status')).toHaveAttribute('data-status', 'connected', { timeout: 15_000 });
  await expect(page.getByTestId('price-AAPL')).toHaveText(/\d/, { timeout: 15_000 });
}

/** Parse "r,g,b" out of an rgb()/rgba() string. */
export function parseRgb(color: string): [number, number, number, number] | null {
  const m = color.match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,\s/]+([\d.]+))?/);
  if (!m) return null;
  return [Number(m[1]), Number(m[2]), Number(m[3]), m[4] === undefined ? 1 : Number(m[4])];
}
