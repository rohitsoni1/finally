import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TradeBar } from "./TradeBar";
import { ApiError } from "@/lib/api";
import { portfolio } from "@/test/fixtures";

describe("TradeBar", () => {
  it("pre-fills the selected ticker and validates quantity", async () => {
    const onTrade = vi.fn();
    render(<TradeBar selected="AAPL" lastPrice={190} cash={10000} onTrade={onTrade} />);
    expect(screen.getByTestId("trade-ticker")).toHaveValue("AAPL");
    fireEvent.click(screen.getByTestId("trade-buy"));
    expect(await screen.findByTestId("trade-error")).toHaveTextContent(/quantity/i);
    expect(onTrade).not.toHaveBeenCalled();
  });

  it("submits buys and sells and reports the fill", async () => {
    const onTrade = vi.fn(async (ticker: string, quantity: number, side: "buy" | "sell") => ({
      trade: { id: "1", ticker, side, quantity, price: 190.12, executed_at: "" },
      portfolio,
    }));
    render(<TradeBar selected="AAPL" lastPrice={190} cash={10000} onTrade={onTrade} />);
    fireEvent.change(screen.getByTestId("trade-quantity"), { target: { value: "5" } });
    fireEvent.click(screen.getByTestId("trade-sell"));
    expect(await screen.findByTestId("trade-status")).toHaveTextContent("Sold 5 AAPL at $190.12");
    expect(onTrade).toHaveBeenCalledWith("AAPL", 5, "sell");
    expect(screen.getByTestId("trade-quantity")).toHaveValue("");
  });

  it("shows server validation errors inline", async () => {
    const onTrade = vi.fn(async () => {
      throw new ApiError("Insufficient cash", 400);
    });
    render(<TradeBar selected={null} lastPrice={null} cash={10} onTrade={onTrade} />);
    fireEvent.change(screen.getByTestId("trade-ticker"), { target: { value: "nvda" } });
    fireEvent.change(screen.getByTestId("trade-quantity"), { target: { value: "1000" } });
    fireEvent.click(screen.getByTestId("trade-buy"));
    expect(await screen.findByTestId("trade-error")).toHaveTextContent("Insufficient cash");
    expect(onTrade).toHaveBeenCalledWith("NVDA", 1000, "buy");
  });
});
