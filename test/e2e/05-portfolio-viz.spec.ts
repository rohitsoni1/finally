import { expect, test } from '@playwright/test';
import { flatten, getPortfolio, openApp, parseRgb, trade, waitForPrice } from './helpers';

/** PLAN §12 "Portfolio visualization: heatmap renders with correct colors, P&L chart has data points". */
test.describe('Portfolio visualization', () => {
  test.beforeAll(async ({ request }) => {
    for (const t of ['NVDA', 'JPM']) {
      await waitForPrice(request, t);
      await flatten(request, t);
    }
    expect((await trade(request, 'NVDA', 'buy', 5)).status()).toBe(200);
    expect((await trade(request, 'JPM', 'buy', 3)).status()).toBe(200);
  });

  test.afterAll(async ({ request }) => {
    await flatten(request, 'NVDA');
    await flatten(request, 'JPM');
  });

  test('heatmap renders a tile per position, sized by weight and coloured by P&L', async ({ page, request }) => {
    await openApp(page);
    const heatmap = page.getByTestId('heatmap');
    await expect(heatmap).toBeVisible();
    const box = await heatmap.boundingBox();
    expect(box!.width).toBeGreaterThan(50);
    expect(box!.height).toBeGreaterThan(50);

    const portfolio = await getPortfolio(request);
    expect(portfolio.positions.length).toBeGreaterThanOrEqual(2);
    for (const p of portfolio.positions) await expect(heatmap).toContainText(p.ticker);

    // Tiles: `heatmap-tile-<TICKER>` (one per position).
    const tiles = heatmap.locator('[data-testid^="heatmap-tile-"]');
    await expect(tiles).toHaveCount(portfolio.positions.length);

    // Let prices drift from cost so P&L is non-zero.
    await page.waitForTimeout(3000);
    const latest = await getPortfolio(request);
    const sample = await Promise.all(
      latest.positions.map(async (p) => {
        const tile = heatmap.getByTestId(`heatmap-tile-${p.ticker}`);
        const bb = (await tile.boundingBox())!;
        const bg = await tile.evaluate((el) => getComputedStyle(el).backgroundColor);
        const rgb = parseRgb(bg);
        expect(rgb, `tile ${p.ticker} has no background colour (${bg})`).not.toBeNull();
        expect(rgb![3], `tile ${p.ticker} background is transparent`).toBeGreaterThan(0);
        return { ...p, area: bb.width * bb.height, greenness: rgb![1] - rgb![0], bg };
      }),
    );

    // Size: a materially heavier position must get a larger tile.
    const byWeight = [...sample].sort((x, y) => y.weight - x.weight);
    for (let i = 1; i < byWeight.length; i++) {
      if (byWeight[i - 1].weight > byWeight[i].weight * 1.3) {
        expect(byWeight[i - 1].area, `${byWeight[i - 1].ticker} (w=${byWeight[i - 1].weight}) tile should be larger than ${byWeight[i].ticker} (w=${byWeight[i].weight})`).toBeGreaterThan(byWeight[i].area);
      }
    }

    // Colour: higher P&L% must never look less green (more red) than a clearly lower P&L%.
    const byPnl = [...sample].sort((x, y) => y.unrealized_pnl_percent - x.unrealized_pnl_percent);
    for (let i = 0; i < byPnl.length; i++) {
      for (let j = i + 1; j < byPnl.length; j++) {
        if (byPnl[i].unrealized_pnl_percent - byPnl[j].unrealized_pnl_percent > 0.3) {
          expect(byPnl[i].greenness, `${byPnl[i].ticker} (${byPnl[i].unrealized_pnl_percent.toFixed(2)}%, ${byPnl[i].bg}) should be greener than ${byPnl[j].ticker} (${byPnl[j].unrealized_pnl_percent.toFixed(2)}%, ${byPnl[j].bg})`).toBeGreaterThanOrEqual(byPnl[j].greenness);
        }
      }
    }
    // A clearly profitable tile is green (G > R); a clearly losing tile is red (R > G).
    for (const t of sample) {
      if (t.unrealized_pnl_percent > 1) expect(t.greenness, `${t.ticker} +${t.unrealized_pnl_percent.toFixed(2)}% tile ${t.bg} should be green`).toBeGreaterThan(0);
      if (t.unrealized_pnl_percent < -1) expect(t.greenness, `${t.ticker} ${t.unrealized_pnl_percent.toFixed(2)}% tile ${t.bg} should be red`).toBeLessThan(0);
    }
  });

  test('P&L chart renders with snapshot data points', async ({ page, request }) => {
    const history = await (await request.get('/api/portfolio/history')).json();
    expect(history.snapshots.length, 'trades should have recorded snapshots').toBeGreaterThanOrEqual(2);

    await openApp(page);
    const chart = page.getByTestId('pnl-chart');
    await expect(chart).toBeVisible();
    const box = await chart.boundingBox();
    expect(box!.width).toBeGreaterThan(100);
    expect(box!.height).toBeGreaterThan(50);
    // Rendered via canvas (Lightweight Charts) or SVG (Recharts) – either must be present and sized.
    const drawn = chart.locator('canvas, svg');
    await expect(drawn.first()).toBeVisible();
    // An SVG chart should contain a drawn path; a canvas chart has non-zero size.
    const svgPaths = await chart.locator('svg path').count();
    const canvases = await chart.locator('canvas').count();
    expect(svgPaths + canvases).toBeGreaterThan(0);
  });

  test('positions table lists every position with P&L columns', async ({ page, request }) => {
    await openApp(page);
    const table = page.getByTestId('positions-table');
    await expect(table).toBeVisible();
    const portfolio = await getPortfolio(request);
    for (const p of portfolio.positions) {
      const row = page.getByTestId(`position-row-${p.ticker}`);
      await expect(row).toBeVisible();
      await expect(row).toContainText(p.ticker);
      await expect(row).toContainText(/\$?\d+[.,]\d{2}/); // prices / P&L rendered
      await expect(row).toContainText('%');
    }
  });
});
