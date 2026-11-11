import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatPanel, hasExecutedActions } from "./ChatPanel";
import { api } from "@/lib/api";
import type { ChatResponse } from "@/lib/types";

afterEach(() => vi.restoreAllMocks());

describe("ChatPanel", () => {
  it("restores history from the server", async () => {
    vi.spyOn(api, "chatHistory").mockResolvedValue([
      { id: "1", role: "user", content: "hi", actions: null, created_at: "" },
      {
        id: "2",
        role: "assistant",
        content: "Bought it",
        actions: { trades: [{ ticker: "AAPL", side: "buy", quantity: 5, status: "executed", price: 190.12 }] },
        created_at: "",
      },
    ]);
    render(<ChatPanel open onToggle={() => {}} onActions={() => {}} />);
    const msgs = await screen.findAllByTestId("chat-message");
    expect(msgs).toHaveLength(2);
    expect(msgs[0]).toHaveAttribute("data-role", "user");
    expect(msgs[1]).toHaveAttribute("data-role", "assistant");
    expect(screen.getByTestId("chat-action")).toHaveTextContent("Bought 5 AAPL at $190.12");
  });

  it("shows a loading indicator, then the reply with inline actions", async () => {
    vi.spyOn(api, "chatHistory").mockResolvedValue([]);
    let resolve!: (r: ChatResponse) => void;
    vi.spyOn(api, "chat").mockReturnValue(new Promise((r) => (resolve = r)));
    const onActions = vi.fn();
    render(<ChatPanel open onToggle={() => {}} onActions={onActions} />);

    fireEvent.change(screen.getByTestId("chat-input"), { target: { value: "Buy 5 AAPL and sell 99 TSLA" } });
    fireEvent.click(screen.getByTestId("chat-send"));

    expect(await screen.findByTestId("chat-loading")).toBeInTheDocument();
    expect(screen.getByTestId("chat-input")).toHaveValue("");
    expect(screen.getAllByTestId("chat-message")[0]).toHaveTextContent("Buy 5 AAPL");

    resolve({
      message: "Done.",
      actions: {
        trades: [
          { ticker: "AAPL", side: "buy", quantity: 5, status: "executed", price: 190, error: null },
          { ticker: "TSLA", side: "sell", quantity: 99, status: "failed", error: "Insufficient shares" },
        ],
        watchlist_changes: [{ ticker: "PYPL", action: "add", status: "executed", error: null }],
      },
    });

    await waitFor(() => expect(screen.queryByTestId("chat-loading")).not.toBeInTheDocument());
    const actions = screen.getAllByTestId("chat-action");
    expect(actions).toHaveLength(3);
    expect(actions[1]).toHaveAttribute("data-status", "failed");
    expect(actions[1]).toHaveTextContent("Insufficient shares");
    expect(actions[2]).toHaveTextContent("Added PYPL");
    expect(onActions).toHaveBeenCalledTimes(1);
  });

  it("collapses to a rail", () => {
    vi.spyOn(api, "chatHistory").mockResolvedValue([]);
    render(<ChatPanel open={false} onToggle={() => {}} onActions={() => {}} />);
    expect(screen.getByTestId("chat-panel")).toHaveAttribute("data-open", "false");
    expect(screen.queryByTestId("chat-input")).not.toBeInTheDocument();
  });

  it("only refreshes for executed actions", () => {
    expect(hasExecutedActions(null)).toBe(false);
    expect(hasExecutedActions({ trades: [{ ticker: "A", side: "buy", quantity: 1, status: "failed" }] })).toBe(false);
    expect(hasExecutedActions({ watchlist_changes: [{ ticker: "A", action: "add", status: "executed" }] })).toBe(true);
  });
});
