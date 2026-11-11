import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Header } from "./Header";
import { Heatmap } from "./Heatmap";
import { PositionsTable } from "./PositionsTable";
import { portfolio } from "@/test/fixtures";

describe("Header", () => {
  it("shows totals and the connection state", () => {
    render(
      <Header totalValue={10005} cash={8100} pnl={5} pnlPercent={0.263} positionsValue={1905} status="reconnecting" />,
    );
    expect(screen.getByTestId("total-value")).toHaveTextContent("$10,005.00");
    expect(screen.getByTestId("cash-balance")).toHaveTextContent("$8,100.00");
    expect(screen.getByTestId("connection-status")).toHaveAttribute("data-status", "reconnecting");
  });
});

describe("portfolio views", () => {
  const positions = [
    ...portfolio.positions,
    { ...portfolio.positions[0], ticker: "TSLA", market_value: 500, unrealized_pnl: -20, unrealized_pnl_percent: -3.8 },
  ];

  it("heatmap renders one tile per position, coloured by P&L", () => {
    render(<Heatmap positions={positions} selected={null} onSelect={() => {}} />);
    expect(screen.getByTestId("heatmap")).toHaveAttribute("data-count", "2");
    expect(screen.getByTestId("heatmap-tile-AAPL")).toHaveAttribute("data-pnl", "up");
    expect(screen.getByTestId("heatmap-tile-TSLA")).toHaveAttribute("data-pnl", "down");
  });

  it("heatmap shows an empty state", () => {
    render(<Heatmap positions={[]} selected={null} onSelect={() => {}} />);
    expect(screen.getByTestId("heatmap")).toHaveTextContent(/No positions yet/);
  });

  it("positions table lists each holding with P&L", () => {
    render(<PositionsTable positions={positions} selected={null} onSelect={() => {}} />);
    const row = screen.getByTestId("position-row-AAPL");
    expect(row).toHaveTextContent("10");
    expect(row).toHaveTextContent("190.00");
    expect(row).toHaveTextContent("+$5.00");
    expect(screen.getByTestId("position-row-TSLA")).toHaveTextContent("−3.80%");
  });
});
