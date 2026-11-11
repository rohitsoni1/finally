import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Watchlist } from "./Watchlist";
import { priceUpdate } from "@/test/fixtures";

const entries = [
  { ticker: "AAPL", added_at: "", price: priceUpdate("AAPL", 190, { day_change_percent: 1.25 }) },
  { ticker: "MSFT", added_at: "", price: null },
];

function setup(overrides = {}) {
  const props = {
    entries,
    prices: { MSFT: priceUpdate("MSFT", 420, { day_change_percent: -0.5 }) },
    history: {},
    selected: "AAPL",
    onSelect: vi.fn(),
    onAdd: vi.fn(async () => {}),
    onRemove: vi.fn(async () => {}),
    ...overrides,
  };
  render(<Watchlist {...props} />);
  return props;
}

describe("Watchlist", () => {
  it("renders rows with streamed or initial prices and daily change", () => {
    setup();
    expect(screen.getByTestId("watchlist-row-AAPL")).toHaveTextContent("+1.25%");
    expect(screen.getByTestId("price-AAPL")).toHaveTextContent("190.00");
    expect(screen.getByTestId("price-MSFT")).toHaveTextContent("420.00");
    expect(screen.getByTestId("watchlist-row-MSFT")).toHaveTextContent("−0.50%");
  });

  it("selects a ticker on click", () => {
    const p = setup();
    fireEvent.click(screen.getByTestId("watchlist-row-MSFT"));
    expect(p.onSelect).toHaveBeenCalledWith("MSFT");
  });

  it("adds a normalized ticker and clears the input", async () => {
    const p = setup();
    const input = screen.getByTestId("watchlist-add-input");
    fireEvent.change(input, { target: { value: " pypl " } });
    fireEvent.click(screen.getByTestId("watchlist-add-button"));
    await waitFor(() => expect(p.onAdd).toHaveBeenCalledWith("PYPL"));
    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("shows server errors when adding fails", async () => {
    setup({ onAdd: vi.fn(async () => Promise.reject(new Error("PYPL is already on the watchlist"))) });
    fireEvent.change(screen.getByTestId("watchlist-add-input"), { target: { value: "PYPL" } });
    fireEvent.click(screen.getByTestId("watchlist-add-button"));
    expect(await screen.findByRole("alert")).toHaveTextContent("already on the watchlist");
  });

  it("removes without selecting the row", () => {
    const p = setup();
    fireEvent.click(screen.getByTestId("watchlist-remove-MSFT"));
    expect(p.onRemove).toHaveBeenCalledWith("MSFT");
    expect(p.onSelect).not.toHaveBeenCalled();
  });
});
