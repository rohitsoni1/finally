import { expect, test } from '@playwright/test';
import { DEFAULT_TICKERS, ensureWatchlist, flatten, getPortfolio, getPosition, trade, waitForPrice } from './helpers';

/** Contract-level checks against planning/API_CONTRACT.md §3 / §4. */

const PRICE_KEYS = [
  'ticker',
  'price',
  'previous_price',
  'reference_price',
  'change',
  'change_percent',
  'day_change',
  'day_change_percent',
  'direction',
  'timestamp',
];

function expectPriceUpdate(p: any, ticker?: string) {
  for (const k of PRICE_KEYS) expect(p, `PriceUpdate missing "${k}"`).toHaveProperty(k);
  if (ticker) expect(p.ticker).toBe(ticker);
  expect(typeof p.price).toBe('number');
  expect(p.price).toBeGreaterThan(0);
  expect(['up', 'down', 'flat']).toContain(p.direction);
  expect(typeof p.timestamp).toBe('number');
}

function expectPortfolioShape(p: any) {
  for (const k of ['cash_balance', 'positions_value', 'total_value', 'total_cost', 'unrealized_pnl', 'unrealized_pnl_percent', 'positions']) {
    expect(p, `portfolio missing "${k}"`).toHaveProperty(k);
  }
  expect(Array.isArray(p.positions)).toBe(true);
  expect(p.total_value).toBeCloseTo(p.cash_balance + p.positions_value, 2);
  for (const pos of p.positions) {
    for (const k of ['ticker', 'quantity', 'avg_cost', 'current_price', 'market_value', 'cost_basis', 'unrealized_pnl', 'unrealized_pnl_percent', 'weight']) {
      expect(pos, `position missing "${k}"`).toHaveProperty(k);
    }
    expect(pos.weight).toBeGreaterThanOrEqual(0);
    expect(pos.weight).toBeLessThanOrEqual(1);
    expect(pos.market_value).toBeCloseTo(pos.quantity * pos.current_price, 2);
    expect(pos.cost_basis).toBeCloseTo(pos.quantity * pos.avg_cost, 2);
  }
}

/** Read the first SSE `data:` payload from /api/stream/prices using Node fetch. */
async function readFirstSseEvent(baseURL: string): Promise<{ contentType: string | null; data: any }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10_000);
  try {
    const res = await fetch(new URL('/api/stream/prices', baseURL), { signal: controller.signal, headers: { Accept: 'text/event-stream' } });
    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) throw new Error('SSE stream ended before any data event');
      buf += decoder.decode(value, { stream: true });
      const events = buf.split(/\r?\n\r?\n/);
      for (const ev of events.slice(0, -1)) {
        const dataLines = ev.split(/\r?\n/).filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trim());
        if (dataLines.length) {
          const data = JSON.parse(dataLines.join('\n'));
          if (data && Object.keys(data).length) return { contentType: res.headers.get('content-type'), data };
        }
      }
    }
  } finally {
    clearTimeout(timer);
    controller.abort();
  }
}

test.describe('API contract', () => {
  test('GET /api/health', async ({ request }) => {
    const res = await request.get('/api/health');
    expect(res.status()).toBe(200);
    expect((await res.json()).status).toBe('ok');
  });

  test('GET /api/stream/prices is SSE with PriceUpdate objects keyed by ticker', async ({ baseURL }) => {
    const { contentType, data } = await readFirstSseEvent(baseURL!);
    expect(contentType).toContain('text/event-stream');
    for (const t of DEFAULT_TICKERS) {
      expect(data, `SSE payload missing ${t}`).toHaveProperty(t);
      expectPriceUpdate(data[t], t);
    }
  });

  test('GET /api/watchlist returns tickers with prices', async ({ request }) => {
    await waitForPrice(request, 'AAPL');
    const res = await request.get('/api/watchlist');
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(Array.isArray(body.tickers)).toBe(true);
    const tickers = body.tickers.map((t: any) => t.ticker);
    for (const t of DEFAULT_TICKERS) expect(tickers).toContain(t);
    for (const row of body.tickers) {
      expect(row).toHaveProperty('added_at');
      expect(row).toHaveProperty('price');
      if (row.price !== null) expectPriceUpdate(row.price, row.ticker);
    }
  });

  test('POST/DELETE /api/watchlist: 201, 409, 400, 200, 404', async ({ request }) => {
    await ensureWatchlist(request, 'PYPL', false);

    let res = await request.post('/api/watchlist', { data: { ticker: 'pypl ' } });
    expect(res.status(), await res.text()).toBe(201);
    const added = await res.json();
    expect(added.ticker).toBe('PYPL'); // normalised to upper-case, trimmed
    expect(added).toHaveProperty('added_at');

    res = await request.post('/api/watchlist', { data: { ticker: 'PYPL' } });
    expect(res.status()).toBe(409);
    expect(await res.json()).toHaveProperty('detail');

    res = await request.post('/api/watchlist', { data: { ticker: 'NOT A TICKER!!' } });
    expect(res.status()).toBe(400);
    expect(await res.json()).toHaveProperty('detail');

    // The newly added ticker should start receiving prices.
    await waitForPrice(request, 'PYPL');

    res = await request.delete('/api/watchlist/PYPL');
    expect(res.status()).toBe(200);
    expect(await res.json()).toEqual({ ticker: 'PYPL', removed: true });

    res = await request.delete('/api/watchlist/PYPL');
    expect(res.status()).toBe(404);
  });

  test('GET /api/portfolio shape', async ({ request }) => {
    expectPortfolioShape(await getPortfolio(request));
  });

  test('POST /api/portfolio/trade buy then sell (full exit deletes position)', async ({ request }) => {
    await waitForPrice(request, 'GOOGL');
    await flatten(request, 'GOOGL');
    const before = await getPortfolio(request);

    let res = await trade(request, 'GOOGL', 'buy', 3);
    expect(res.status(), await res.text()).toBe(200);
    let body = await res.json();
    expect(body.trade).toMatchObject({ ticker: 'GOOGL', side: 'buy', quantity: 3 });
    expect(body.trade.price).toBeGreaterThan(0);
    expect(body.trade).toHaveProperty('id');
    expect(body.trade).toHaveProperty('executed_at');
    expectPortfolioShape(body.portfolio);
    expect(body.portfolio.cash_balance).toBeCloseTo(before.cash_balance - 3 * body.trade.price, 2);
    const pos = body.portfolio.positions.find((p: any) => p.ticker === 'GOOGL');
    expect(pos.quantity).toBeCloseTo(3, 6);
    expect(pos.avg_cost).toBeCloseTo(body.trade.price, 2);

    // fractional partial sell
    res = await trade(request, 'GOOGL', 'sell', 1.5);
    expect(res.status(), await res.text()).toBe(200);
    body = await res.json();
    expect(body.trade.side).toBe('sell');
    expect(body.portfolio.positions.find((p: any) => p.ticker === 'GOOGL').quantity).toBeCloseTo(1.5, 6);

    res = await trade(request, 'GOOGL', 'sell', 1.5);
    expect(res.status(), await res.text()).toBe(200);
    body = await res.json();
    expect(body.portfolio.positions.find((p: any) => p.ticker === 'GOOGL')).toBeUndefined();
    expect(await getPosition(request, 'GOOGL')).toBeUndefined();
  });

  test('POST /api/portfolio/trade validation errors are 400 with detail', async ({ request }) => {
    await flatten(request, 'TSLA');
    const before = await getPortfolio(request);

    const cases = [
      { ticker: 'AAPL', side: 'buy', quantity: 10_000_000 }, // insufficient cash
      { ticker: 'TSLA', side: 'sell', quantity: 1 }, // no shares
      { ticker: 'AAPL', side: 'buy', quantity: 0 }, // non-positive qty
      { ticker: 'AAPL', side: 'buy', quantity: -5 },
      { ticker: 'AAPL', side: 'hold', quantity: 1 }, // bad side
      { ticker: 'ZZZZQQ', side: 'buy', quantity: 1 }, // no price available
    ];
    for (const c of cases) {
      const res = await request.post('/api/portfolio/trade', { data: c });
      expect([400, 422], `${JSON.stringify(c)} -> ${res.status()} ${await res.text()}`).toContain(res.status());
      expect(await res.json()).toHaveProperty('detail');
    }
    // Business-rule failures specifically must be 400.
    for (const c of cases.slice(0, 2)) {
      const res = await request.post('/api/portfolio/trade', { data: c });
      expect(res.status(), JSON.stringify(c)).toBe(400);
      expect(typeof (await res.json()).detail).toBe('string');
    }
    const after = await getPortfolio(request);
    expect(after.cash_balance).toBeCloseTo(before.cash_balance, 6);
  });

  test('GET /api/portfolio/history: snapshots ascending, recorded after trades', async ({ request }) => {
    const before = (await (await request.get('/api/portfolio/history')).json()).snapshots;
    const res = await trade(request, 'AAPL', 'buy', 1);
    expect(res.status()).toBe(200);
    const historyRes = await request.get('/api/portfolio/history');
    expect(historyRes.status()).toBe(200);
    const { snapshots } = await historyRes.json();
    expect(snapshots.length).toBeGreaterThan(before.length);
    for (const s of snapshots) {
      expect(typeof s.total_value).toBe('number');
      expect(typeof s.recorded_at).toBe('string');
    }
    const times = snapshots.map((s: any) => Date.parse(s.recorded_at));
    for (let i = 1; i < times.length; i++) expect(times[i]).toBeGreaterThanOrEqual(times[i - 1]);
    await flatten(request, 'AAPL');
  });

  test('POST /api/chat mock: plain message', async ({ request }) => {
    const res = await request.post('/api/chat', { data: { message: 'hello there' } });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.message).toBe('Mock response: I received your message.');
    expect(body.actions?.trades ?? []).toEqual([]);
    expect(body.actions?.watchlist_changes ?? []).toEqual([]);
  });

  test('POST /api/chat mock: buy/sell execute and failures are reported', async ({ request }) => {
    await waitForPrice(request, 'META');
    await flatten(request, 'META');

    let res = await request.post('/api/chat', { data: { message: 'Please buy 2 META' } });
    expect(res.status()).toBe(200);
    let body = await res.json();
    expect(typeof body.message).toBe('string');
    expect(body.actions.trades).toHaveLength(1);
    expect(body.actions.trades[0]).toMatchObject({ ticker: 'META', side: 'buy', quantity: 2, status: 'executed', error: null });
    expect(body.actions.trades[0].price).toBeGreaterThan(0);
    expect((await getPosition(request, 'META'))?.quantity).toBeCloseTo(2, 6);

    res = await request.post('/api/chat', { data: { message: 'sell 100000 META' } });
    expect(res.status()).toBe(200);
    body = await res.json();
    expect(body.actions.trades[0]).toMatchObject({ ticker: 'META', side: 'sell', status: 'failed' });
    expect(body.actions.trades[0].error).toBeTruthy();

    res = await request.post('/api/chat', { data: { message: 'SELL 2 meta' } });
    body = await res.json();
    expect(body.actions.trades[0]).toMatchObject({ ticker: 'META', side: 'sell', status: 'executed' });
    expect(await getPosition(request, 'META')).toBeUndefined();
  });

  test('POST /api/chat mock: watchlist add/remove', async ({ request }) => {
    await ensureWatchlist(request, 'SHOP', false);
    let res = await request.post('/api/chat', { data: { message: 'add SHOP' } });
    let body = await res.json();
    expect(body.actions.watchlist_changes[0]).toMatchObject({ ticker: 'SHOP', action: 'add', status: 'executed' });
    expect((await (await request.get('/api/watchlist')).json()).tickers.map((t: any) => t.ticker)).toContain('SHOP');

    res = await request.post('/api/chat', { data: { message: 'remove SHOP' } });
    body = await res.json();
    expect(body.actions.watchlist_changes[0]).toMatchObject({ ticker: 'SHOP', action: 'remove', status: 'executed' });
    expect((await (await request.get('/api/watchlist')).json()).tickers.map((t: any) => t.ticker)).not.toContain('SHOP');
  });

  test('GET /api/chat/history returns stored messages ascending', async ({ request }) => {
    await request.post('/api/chat', { data: { message: 'history check' } });
    const res = await request.get('/api/chat/history');
    expect(res.status()).toBe(200);
    const { messages } = await res.json();
    expect(messages.length).toBeGreaterThanOrEqual(2);
    for (const m of messages) {
      for (const k of ['id', 'role', 'content', 'actions', 'created_at']) expect(m).toHaveProperty(k);
      expect(['user', 'assistant']).toContain(m.role);
    }
    const last2 = messages.slice(-2);
    expect(last2[0]).toMatchObject({ role: 'user', content: 'history check' });
    expect(last2[1].role).toBe('assistant');
    const times = messages.map((m: any) => Date.parse(m.created_at));
    for (let i = 1; i < times.length; i++) expect(times[i]).toBeGreaterThanOrEqual(times[i - 1]);
  });

  test('non-API paths fall back to the SPA index.html', async ({ request }) => {
    const res = await request.get('/some/client/route');
    expect(res.status()).toBe(200);
    expect(res.headers()['content-type']).toContain('text/html');
    const unknownApi = await request.get('/api/does-not-exist');
    expect(unknownApi.status()).toBe(404);
  });
});
