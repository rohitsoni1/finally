import { Page, expect, test } from '@playwright/test';
import { openApp } from './helpers';

const STREAM = '**/api/stream/prices';
const status = (page: Page) => page.getByTestId('connection-status');
const snapshotPrices = async (page: Page) =>
  (await Promise.all(['AAPL', 'MSFT', 'NVDA', 'TSLA', 'GOOGL'].map((t) => page.getByTestId(`price-${t}`).innerText()))).join('|');

/** PLAN §12 "SSE resilience: disconnect and verify reconnection". */
test.describe('SSE resilience', () => {
  test('stream unavailable at load -> not connected; stream restored -> reconnects and prices flow', async ({ page }) => {
    await page.route(STREAM, (route) => route.abort('connectionrefused'));
    await page.goto('/');
    await expect(page.getByTestId('watchlist')).toBeVisible();
    await expect(status(page)).toHaveAttribute('data-status', /reconnecting|disconnected/, { timeout: 15_000 });

    await page.unroute(STREAM);
    await expect(status(page)).toHaveAttribute('data-status', 'connected', { timeout: 30_000 });
    const before = await snapshotPrices(page);
    await expect.poll(() => snapshotPrices(page), { timeout: 15_000, message: 'prices did not flow after reconnect' }).not.toBe(before);
  });

  test('connection drops mid-session -> indicator changes; stream restored -> reconnects', async ({ page }) => {
    // Track EventSource instances so the test can sever the live connection. Playwright's
    // context.setOffline() does not tear down an already-open streaming response, so we close it
    // from the page and fire the same 'error' event the browser emits when a stream dies.
    await page.addInitScript(() => {
      const Orig = window.EventSource;
      const instances: EventSource[] = [];
      class Tracked extends Orig {
        constructor(url: string | URL, init?: EventSourceInit) {
          super(url, init);
          instances.push(this);
        }
      }
      (window as any).EventSource = Tracked;
      (window as any).__dropSSE = () => {
        for (const es of instances) {
          if (es.readyState !== Orig.CLOSED) {
            es.close();
            es.dispatchEvent(new Event('error'));
          }
        }
      };
    });
    await openApp(page);

    // Block reconnection attempts at the network level, then drop the live stream.
    let blocked = 0;
    await page.route(STREAM, (route) => {
      blocked++;
      return route.abort('internetdisconnected');
    });
    await page.evaluate(() => (window as any).__dropSSE());
    await expect(status(page)).toHaveAttribute('data-status', /reconnecting|disconnected/, { timeout: 15_000 });

    // Prices must stop updating while disconnected, and the client must keep retrying.
    const frozen = await page.getByTestId('price-AAPL').innerText();
    await expect.poll(() => blocked, { timeout: 15_000, message: 'client never retried the stream' }).toBeGreaterThan(0);
    expect(await page.getByTestId('price-AAPL').innerText()).toBe(frozen);
    await expect(status(page)).not.toHaveAttribute('data-status', 'connected');

    await page.unroute(STREAM);
    await expect(status(page)).toHaveAttribute('data-status', 'connected', { timeout: 30_000 });

    // Prices flow again after reconnection.
    const afterReconnect = await snapshotPrices(page);
    await expect.poll(() => snapshotPrices(page), { timeout: 15_000, message: 'prices did not resume after reconnect' }).not.toBe(afterReconnect);
  });

  test('browser offline/online keeps or restores a working stream', async ({ page, context }) => {
    await openApp(page);
    await context.setOffline(true);
    await page.waitForTimeout(2000);
    await context.setOffline(false);
    await expect(status(page)).toHaveAttribute('data-status', 'connected', { timeout: 30_000 });
    const before = await snapshotPrices(page);
    await expect.poll(() => snapshotPrices(page), { timeout: 15_000, message: 'prices did not flow after going back online' }).not.toBe(before);
  });
});
