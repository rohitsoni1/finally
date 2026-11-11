import { expect, test } from '@playwright/test';
import { ensureWatchlist, getWatchlistTickers, openApp, parseMoney } from './helpers';

/** PLAN §12 "Add and remove a ticker from the watchlist". */
test.describe('Watchlist management', () => {
  const TICKER = 'PYPL';

  test.beforeEach(async ({ request }) => {
    await ensureWatchlist(request, TICKER, false);
  });

  test.afterAll(async ({ request }) => {
    await ensureWatchlist(request, TICKER, false);
  });

  test('add a ticker via the UI, it streams prices, then remove it', async ({ page, request }) => {
    await openApp(page);
    const rows = page.locator('[data-testid^="watchlist-row-"]');
    const initialCount = await rows.count();
    await expect(page.getByTestId(`watchlist-row-${TICKER}`)).toHaveCount(0);

    await page.getByTestId('watchlist-add-input').fill(TICKER.toLowerCase());
    await page.getByTestId('watchlist-add-button').click();

    const row = page.getByTestId(`watchlist-row-${TICKER}`);
    await expect(row).toBeVisible();
    await expect(rows).toHaveCount(initialCount + 1);
    expect(await getWatchlistTickers(request)).toContain(TICKER);

    // New ticker receives live prices over the existing SSE stream.
    await expect
      .poll(async () => parseMoney(await page.getByTestId(`price-${TICKER}`).innerText()), {
        timeout: 15_000,
        message: `no price shown for newly added ${TICKER}`,
      })
      .toBeGreaterThan(0);

    // Survives a reload (persisted server-side).
    await page.reload();
    await expect(page.getByTestId(`watchlist-row-${TICKER}`)).toBeVisible();

    await page.getByTestId(`watchlist-remove-${TICKER}`).click();
    await expect(page.getByTestId(`watchlist-row-${TICKER}`)).toHaveCount(0);
    await expect(rows).toHaveCount(initialCount);
    expect(await getWatchlistTickers(request)).not.toContain(TICKER);

    await page.reload();
    await expect(page.getByTestId('watchlist')).toBeVisible();
    await expect(page.getByTestId(`watchlist-row-${TICKER}`)).toHaveCount(0);
  });

  test('adding a duplicate ticker does not create a second row', async ({ page }) => {
    await openApp(page);
    await page.getByTestId('watchlist-add-input').fill('AAPL');
    await page.getByTestId('watchlist-add-button').click();
    // Give the request time to complete, then verify there is still exactly one AAPL row.
    await page.waitForTimeout(1000);
    await expect(page.getByTestId('watchlist-row-AAPL')).toHaveCount(1);
  });
});
