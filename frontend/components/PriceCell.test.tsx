import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FLASH_HOLD_MS, PriceCell } from "./PriceCell";

describe("PriceCell", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("does not flash on first render", () => {
    render(<PriceCell price={190} testId="p" />);
    const el = screen.getByTestId("p");
    expect(el).toHaveTextContent("190.00");
    expect(el.className).not.toMatch(/flash-/);
  });

  it("flashes green on an uptick, then removes the class so it fades", () => {
    const { rerender } = render(<PriceCell price={190} testId="p" />);
    rerender(<PriceCell price={190.5} testId="p" />);
    const el = screen.getByTestId("p");
    expect(el).toHaveClass("flash-up");
    expect(el).toHaveTextContent("190.50");
    act(() => vi.advanceTimersByTime(FLASH_HOLD_MS + 10));
    expect(el).not.toHaveClass("flash-up");
    expect(el).toHaveClass("price-cell");
  });

  it("flashes red on a downtick and ignores unchanged prices", () => {
    const { rerender } = render(<PriceCell price={190} testId="p" />);
    rerender(<PriceCell price={189} testId="p" />);
    expect(screen.getByTestId("p")).toHaveClass("flash-down");
    act(() => vi.advanceTimersByTime(FLASH_HOLD_MS + 10));
    rerender(<PriceCell price={189} testId="p" />);
    expect(screen.getByTestId("p").className).not.toMatch(/flash-/);
  });
});
