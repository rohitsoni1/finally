import { Page, expect, test } from '@playwright/test';
import { ensureWatchlist, flatten, getPosition, getWatchlistTickers, openApp, waitForPrice } from './helpers';

async function sendChat(page: Page, text: string) {
  const assistant = page.locator('[data-testid="chat-message"][data-role="assistant"]');
  const before = await assistant.count();
  await page.getByTestId('chat-input').fill(text);
  await page.getByTestId('chat-send').click();
  await expect(page.locator('[data-testid="chat-message"][data-role="user"]').last()).toContainText(text);
  await expect(assistant).toHaveCount(before + 1, { timeout: 20_000 });
  await expect(page.getByTestId('chat-loading')).toHaveCount(0);
  return assistant.last();
}

/** PLAN §12 "AI chat (mocked): send a message, receive a response, trade execution appears inline". */
test.describe('AI chat (LLM_MOCK=true)', () => {
  test.beforeAll(async ({ request }) => {
    await waitForPrice(request, 'AMZN');
    await flatten(request, 'AMZN');
    await ensureWatchlist(request, 'PYPL', false);
  });

  test.afterAll(async ({ request }) => {
    await flatten(request, 'AMZN');
    await ensureWatchlist(request, 'PYPL', false);
  });

  test('send a message and receive the mock response', async ({ page }) => {
    await openApp(page);
    await expect(page.getByTestId('chat-panel')).toBeVisible();
    const reply = await sendChat(page, 'How is my portfolio doing?');
    await expect(reply).toContainText('Mock response: I received your message.');
    await expect(page.getByTestId('chat-input')).toHaveValue('');
  });

  test('loading indicator shows while waiting for the response', async ({ page }) => {
    await openApp(page);
    // Delay the chat response so the indicator is observable.
    await page.route('**/api/chat', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      await new Promise((r) => setTimeout(r, 1500));
      await route.continue();
    });
    await page.getByTestId('chat-input').fill('slow hello');
    await page.getByTestId('chat-send').click();
    await expect(page.getByTestId('chat-loading')).toBeVisible();
    await expect(page.getByTestId('chat-loading')).toHaveCount(0, { timeout: 15_000 });
    await expect(page.locator('[data-testid="chat-message"][data-role="assistant"]').last()).toContainText('Mock response');
  });

  test('chat buy executes a trade, shows it inline, and updates the portfolio', async ({ page, request }) => {
    await openApp(page);
    await expect(page.getByTestId('position-row-AMZN')).toHaveCount(0);
    const reply = await sendChat(page, 'buy 2 AMZN');

    const action = page.getByTestId('chat-action').filter({ hasText: 'AMZN' }).last();
    await expect(action).toBeVisible();
    await expect(action).toContainText(/buy|bought/i);

    expect((await getPosition(request, 'AMZN'))?.quantity).toBeCloseTo(2, 6);
    // Frontend refreshes the portfolio after chat-driven trades (no reload).
    await expect(page.getByTestId('position-row-AMZN')).toBeVisible();
    await expect(reply).not.toBeEmpty();
  });

  test('chat sell that fails validation shows the failure inline', async ({ page, request }) => {
    await openApp(page);
    await sendChat(page, 'sell 999999 AMZN');
    const action = page.getByTestId('chat-action').filter({ hasText: 'AMZN' }).last();
    await expect(action).toBeVisible();
    await expect(action).toContainText(/fail|insufficient|error|not enough|cannot|unable/i);
    expect((await getPosition(request, 'AMZN'))?.quantity).toBeCloseTo(2, 6);
  });

  test('chat watchlist add/remove updates the watchlist panel', async ({ page, request }) => {
    await openApp(page);
    await sendChat(page, 'add PYPL');
    await expect(page.getByTestId('chat-action').filter({ hasText: 'PYPL' }).last()).toBeVisible();
    await expect(page.getByTestId('watchlist-row-PYPL')).toBeVisible();
    expect(await getWatchlistTickers(request)).toContain('PYPL');

    await sendChat(page, 'remove PYPL');
    await expect(page.getByTestId('watchlist-row-PYPL')).toHaveCount(0);
    expect(await getWatchlistTickers(request)).not.toContain('PYPL');
  });

  test('conversation history is restored after reload', async ({ page }) => {
    await openApp(page);
    await sendChat(page, 'remember this message');
    await page.reload();
    await expect(page.getByTestId('chat-panel')).toBeVisible();
    await expect(page.locator('[data-testid="chat-message"][data-role="user"]').filter({ hasText: 'remember this message' })).toHaveCount(1);
  });
});
