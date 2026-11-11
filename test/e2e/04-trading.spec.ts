import { Page, expect, test } from '@playwright/test';
import { flatten, getPortfolio, getPosition, openApp, textNumber, trade, waitForPrice } from './helpers';

async function submitTrade(page: Page, ticker: string, quantity: number | string, side: 'buy' | 'sell') {
  await page.getByTestId('trade-ticker').fill(ticker);
  await page.getByTestId('trade-quantity').fill(String(quantity));
  await page.getByTestId(side === 'buy' ? 'trade-buy' : 'trade-sell').click();
}

/** PLAN §12 "Buy shares" and "Sell shares". */
test.describe('Trading', () => {
  test.beforeEach(async ({ request }) => {
    await waitForPrice(request, 'AAPL');
    await waitForPrice(request, 'MSFT');
    await flatten(request, 'AAPL');
    await flatten(request, 'MSFT');
  });

  test('buy shares: cash decreases and a position appears', async ({ page, request }) => {
    await openApp(page);
    const cashBefore = await textNumber(page.getByTestId('cash-balance'));
    const apiBefore = await getPortfolio(request);
    expect(cashBefore).toBeCloseTo(apiBefore.cash_balance, 1);
    await expect(page.getByTestId('position-row-AAPL')).toHaveCount(0);

    await submitTrade(page, 'AAPL', 5, 'buy');

    await expect(page.getByTestId('positions-table')).toBeVisible();
    const row = page.getByTestId('position-row-AAPL');
    await expect(row).toBeVisible();
    await expect(row).toContainText('AAPL');
    await expect(row).toContainText('5');

    // Cash in header reflects the fill price (prices fluctuate, so compare against the API's recorded trade).
    const pos = await getPosition(request, 'AAPL');
    expect(pos?.quantity).toBeCloseTo(5, 6);
    const apiAfter = await getPortfolio(request);
    expect(apiAfter.cash_balance).toBeCloseTo(apiBefore.cash_balance - 5 * pos!.avg_cost, 2);
    await expect.poll(async () => textNumber(page.getByTestId('cash-balance'))).toBeCloseTo(apiAfter.cash_balance, 1);
    expect(await textNumber(page.getByTestId('cash-balance'))).toBeLessThan(cashBefore);
    await expect(page.getByTestId('trade-error')).toHaveCount(0);
  });

  test('sell shares: cash increases, position updates, then disappears on full exit', async ({ page, request }) => {
    const res = await trade(request, 'MSFT', 'buy', 4);
    expect(res.status(), await res.text()).toBe(200);

    await openApp(page);
    const row = page.getByTestId('position-row-MSFT');
    await expect(row).toBeVisible();
    const cashBefore = await textNumber(page.getByTestId('cash-balance'));

    // Partial sell
    await submitTrade(page, 'MSFT', 1, 'sell');
    await expect.poll(async () => (await getPosition(request, 'MSFT'))?.quantity).toBeCloseTo(3, 6);
    await expect.poll(async () => textNumber(page.getByTestId('cash-balance'))).toBeGreaterThan(cashBefore);
    await expect(row).toBeVisible();
    await expect(row).toContainText('3');
    const cashMid = await textNumber(page.getByTestId('cash-balance'));

    // Full exit
    await submitTrade(page, 'MSFT', 3, 'sell');
    await expect(row).toHaveCount(0);
    expect(await getPosition(request, 'MSFT')).toBeUndefined();
    await expect.poll(async () => textNumber(page.getByTestId('cash-balance'))).toBeGreaterThan(cashMid);
    const api = await getPortfolio(request);
    await expect.poll(async () => textNumber(page.getByTestId('cash-balance'))).toBeCloseTo(api.cash_balance, 1);
  });

  test('insufficient cash shows a trade error and changes nothing', async ({ page, request }) => {
    await openApp(page);
    const before = await getPortfolio(request);
    await submitTrade(page, 'AAPL', 1_000_000, 'buy');
    await expect(page.getByTestId('trade-error')).toBeVisible();
    await expect(page.getByTestId('trade-error')).not.toBeEmpty();
    await expect(page.getByTestId('position-row-AAPL')).toHaveCount(0);
    const after = await getPortfolio(request);
    expect(after.cash_balance).toBeCloseTo(before.cash_balance, 6);
    expect(await textNumber(page.getByTestId('cash-balance'))).toBeCloseTo(before.cash_balance, 1);
  });

  test('selling shares you do not own shows a trade error', async ({ page }) => {
    await openApp(page);
    await submitTrade(page, 'MSFT', 1, 'sell');
    await expect(page.getByTestId('trade-error')).toBeVisible();
    await expect(page.getByTestId('position-row-MSFT')).toHaveCount(0);
  });

  test('header total value = cash + positions value and tracks live prices', async ({ page, request }) => {
    const res = await trade(request, 'AAPL', 'buy', 10);
    expect(res.status()).toBe(200);
    await openApp(page);
    const api = await getPortfolio(request);
    const cash = await textNumber(page.getByTestId('cash-balance'));
    expect(cash).toBeCloseTo(api.cash_balance, 1);
    // Total value fluctuates with AAPL; it must be within a sane band of the API value.
    const total = await textNumber(page.getByTestId('total-value'));
    expect(total).toBeGreaterThan(cash);
    expect(Math.abs(total - api.total_value)).toBeLessThan(api.positions_value * 0.2 + 1);
    // And it should update live as prices move.
    await expect
      .poll(async () => textNumber(page.getByTestId('total-value')), { timeout: 20_000, message: 'header total value never updated with live prices' })
      .not.toBeCloseTo(total, 2);
  });
});
