import { expect, test } from '@playwright/test';
import { DEFAULT_TICKERS, getPortfolio, openApp, parseMoney, textNumber } from './helpers';

/**
 * PLAN §12 "Fresh start": default watchlist appears, $10k balance shown, prices are streaming.
 * Requires a freshly seeded database (run-local.sh / docker-compose.test.yml both provide one),
 * and runs first in the suite.
 */
test.describe('Fresh start', () => {
  test('API reports the seeded default state', async ({ request }) => {
    const portfolio = await getPortfolio(request);
    expect(portfolio.cash_balance).toBe(10000);
    expect(portfolio.positions).toEqual([]);
    expect(portfolio.total_value).toBeCloseTo(10000, 2);
  });

  test('shows the 10 default tickers', async ({ page }) => {
    await openApp(page);
    const watchlist = page.getByTestId('watchlist');
    for (const t of DEFAULT_TICKERS) {
      await expect(watchlist.getByTestId(`watchlist-row-${t}`)).toBeVisible();
    }
    await expect(page.locator('[data-testid^="watchlist-row-"]')).toHaveCount(DEFAULT_TICKERS.length);
  });

  test('shows $10,000 cash and total value in the header', async ({ page }) => {
    await openApp(page);
    await expect(page.getByTestId('cash-balance')).toContainText(/10,?000/);
    expect(await textNumber(page.getByTestId('cash-balance'))).toBeCloseTo(10000, 2);
    // No positions yet, so total value == cash.
    await expect.poll(async () => textNumber(page.getByTestId('total-value'))).toBeCloseTo(10000, 0);
  });

  test('connection indicator is green/connected', async ({ page }) => {
    await openApp(page);
    await expect(page.getByTestId('connection-status')).toHaveAttribute('data-status', 'connected');
  });

  test('prices are streaming and change over time', async ({ page }) => {
    await openApp(page);
    const initial: Record<string, string> = {};
    for (const t of DEFAULT_TICKERS) {
      initial[t] = await page.getByTestId(`price-${t}`).innerText();
      expect(parseMoney(initial[t]), `price-${t} should be a positive number`).toBeGreaterThan(0);
    }
    // At least one displayed price must change within a few seconds (simulator ticks ~500ms).
    await expect
      .poll(
        async () => {
          let changed = 0;
          for (const t of DEFAULT_TICKERS) {
            if ((await page.getByTestId(`price-${t}`).innerText()) !== initial[t]) changed++;
          }
          return changed;
        },
        { timeout: 15_000, message: 'no watchlist price changed - SSE stream not updating the UI' },
      )
      .toBeGreaterThan(0);
  });

  test('clicking a ticker selects it in the main chart', async ({ page }) => {
    await openApp(page);
    await expect(page.getByTestId('main-chart')).toBeVisible();
    await page.getByTestId('watchlist-row-NVDA').click();
    await expect(page.getByTestId('main-chart')).toContainText('NVDA');
    await page.getByTestId('watchlist-row-JPM').click();
    await expect(page.getByTestId('main-chart')).toContainText('JPM');
  });
});
