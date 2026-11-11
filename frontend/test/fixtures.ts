import type { Portfolio, PriceUpdate } from "@/lib/types";

export function priceUpdate(ticker: string, price: number, extra: Partial<PriceUpdate> = {}): PriceUpdate {
  return {
    ticker,
    price,
    previous_price: price,
    reference_price: price,
    change: 0,
    change_percent: 0,
    day_change: 0,
    day_change_percent: 0,
    direction: "flat",
    timestamp: 1_762_540_800,
    ...extra,
  };
}

export const portfolio: Portfolio = {
  cash_balance: 8100,
  positions_value: 1905,
  total_value: 10005,
  total_cost: 1900,
  unrealized_pnl: 5,
  unrealized_pnl_percent: 0.263,
  positions: [
    {
      ticker: "AAPL",
      quantity: 10,
      avg_cost: 190,
      current_price: 190.5,
      market_value: 1905,
      cost_basis: 1900,
      unrealized_pnl: 5,
      unrealized_pnl_percent: 0.263,
      weight: 0.1904,
    },
  ],
};
