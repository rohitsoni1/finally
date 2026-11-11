"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "@/lib/api";
import { formatQty, formatUsd } from "@/lib/format";
import type { ChatActions, ChatMessage } from "@/lib/types";

export function hasExecutedActions(a: ChatActions | null | undefined): boolean {
  if (!a) return false;
  return (
    (a.trades ?? []).some((t) => t.status === "executed") ||
    (a.watchlist_changes ?? []).some((w) => w.status === "executed")
  );
}

export function ActionList({ actions }: { actions: ChatActions | null | undefined }) {
  const trades = actions?.trades ?? [];
  const changes = actions?.watchlist_changes ?? [];
  if (!trades.length && !changes.length) return null;
  return (
    <ul className="mt-2 flex flex-col gap-1">
      {trades.map((t, i) => {
        const ok = t.status === "executed";
        const verb = t.side === "buy" ? (ok ? "Bought" : "Buy") : ok ? "Sold" : "Sell";
        return (
          <li
            key={`t${i}`}
            data-testid="chat-action"
            data-kind="trade"
            data-status={t.status}
            className={`flex flex-wrap items-baseline gap-x-2 border-l-2 bg-bg/60 px-2 py-1 text-[11px] ${ok ? "border-up" : "border-down"}`}
          >
            <span className={ok ? "text-up" : "text-down"}>{ok ? "Filled" : "Failed"}</span>
            <span className="num text-fg">
              {verb} {formatQty(t.quantity)} {t.ticker}
              {ok && t.price != null ? ` at ${formatUsd(t.price)}` : ""}
            </span>
            {!ok && t.error ? <span className="text-muted">{t.error}</span> : null}
          </li>
        );
      })}
      {changes.map((w, i) => {
        const ok = w.status === "executed";
        return (
          <li
            key={`w${i}`}
            data-testid="chat-action"
            data-kind="watchlist"
            data-status={w.status}
            className={`flex flex-wrap items-baseline gap-x-2 border-l-2 bg-bg/60 px-2 py-1 text-[11px] ${ok ? "border-blue" : "border-down"}`}
          >
            <span className={ok ? "text-blue" : "text-down"}>{ok ? "Watchlist" : "Failed"}</span>
            <span className="num text-fg">
              {w.action === "add" ? (ok ? "Added" : "Add") : ok ? "Removed" : "Remove"} {w.ticker}
            </span>
            {!ok && w.error ? <span className="text-muted">{w.error}</span> : null}
          </li>
        );
      })}
    </ul>
  );
}

function MessageBubble({ m }: { m: ChatMessage }) {
  const mine = m.role === "user";
  return (
    <li
      data-testid="chat-message"
      data-role={m.role}
      className={`max-w-[92%] ${mine ? "self-end" : "self-start"}`}
    >
      <div
        className={`whitespace-pre-wrap px-3 py-2 text-[13px] leading-snug ${
          mine ? "bg-raised text-fg" : "border-l-2 border-accent bg-surface text-fg"
        }`}
      >
        {m.content}
        {!mine ? <ActionList actions={m.actions} /> : null}
      </div>
    </li>
  );
}

const SUGGESTIONS = [
  "How concentrated is my portfolio?",
  "Buy 5 NVDA",
  "Add PYPL to my watchlist",
];

interface ChatPanelProps {
  open: boolean;
  onToggle: () => void;
  onActions: () => void;
}

export function ChatPanel({ open, onToggle, onActions }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let alive = true;
    api
      .chatHistory()
      .then((msgs) => {
        // Don't clobber messages sent before history arrived.
        if (alive) setMessages((cur) => (cur.length ? cur : msgs));
      })
      .catch(() => {
        /* history is optional */
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, loading, open]);

  const send = async (text: string) => {
    const content = text.trim();
    if (!content || loading) return;
    const now = new Date().toISOString();
    setMessages((m) => [
      ...m,
      { id: `local-${Date.now()}`, role: "user", content, actions: null, created_at: now },
    ]);
    setDraft("");
    setError(null);
    setLoading(true);
    try {
      const res = await api.chat(content);
      setMessages((m) => [
        ...m,
        {
          id: `local-${Date.now()}-a`,
          role: "assistant",
          content: res.message,
          actions: res.actions ?? null,
          created_at: new Date().toISOString(),
        },
      ]);
      if (hasExecutedActions(res.actions)) onActions();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void send(draft);
  };

  if (!open) {
    return (
      <aside data-testid="chat-panel" data-open="false" className="flex h-full flex-col items-center border border-line bg-surface py-2">
        <button
          type="button"
          onClick={onToggle}
          aria-label="Open FinAlly chat"
          aria-expanded={false}
          className="flex flex-col items-center gap-2 px-2 py-2 text-[12px] text-muted hover:text-accent"
        >
          <svg viewBox="0 0 16 16" className="h-4 w-4" aria-hidden="true">
            <path d="M10 3L5 8l5 5" fill="none" stroke="currentColor" strokeWidth="1.6" />
          </svg>
          <span className="[writing-mode:vertical-rl]">Ask FinAlly</span>
        </button>
      </aside>
    );
  }

  return (
    <aside data-testid="chat-panel" data-open="true" className="flex h-full min-h-0 flex-col border border-line bg-surface">
      <header className="flex h-8 shrink-0 items-center justify-between border-b border-line px-3">
        <h2 className="text-[12px] font-semibold">
          FinAlly <span className="font-normal text-muted">AI copilot</span>
        </h2>
        <button
          type="button"
          onClick={onToggle}
          aria-label="Collapse chat"
          aria-expanded={true}
          className="flex h-6 w-6 items-center justify-center text-muted hover:text-fg"
        >
          <svg viewBox="0 0 16 16" className="h-4 w-4" aria-hidden="true">
            <path d="M6 3l5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.6" />
          </svg>
        </button>
      </header>

      <ul ref={listRef} className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto p-3" aria-live="polite">
        {messages.length === 0 && !loading ? (
          <li className="mt-2 text-[12px] leading-relaxed text-muted">
            <p className="mb-3 text-fg">
              Ask about your positions, risk or P&amp;L. FinAlly can place trades and edit your watchlist for you.
            </p>
            <div className="flex flex-col items-start gap-1.5">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => void send(s)}
                  className="border border-line px-2 py-1 text-left text-[12px] text-muted hover:border-blue hover:text-fg"
                >
                  {s}
                </button>
              ))}
            </div>
          </li>
        ) : null}
        {messages.map((m) => (
          <MessageBubble key={m.id} m={m} />
        ))}
        {loading ? (
          <li data-testid="chat-loading" className="self-start border-l-2 border-accent bg-surface px-3 py-2 text-[12px] text-muted">
            <span className="typing" aria-label="FinAlly is thinking">
              <span>●</span>
              <span>●</span>
              <span>●</span>
            </span>
          </li>
        ) : null}
      </ul>

      {error ? (
        <p role="alert" className="shrink-0 border-t border-line px-3 py-1.5 text-[11px] text-down">
          {error}
        </p>
      ) : null}

      <form onSubmit={onSubmit} className="flex shrink-0 gap-2 border-t border-line p-2">
        <textarea
          ref={inputRef}
          data-testid="chat-input"
          aria-label="Message FinAlly"
          value={draft}
          rows={2}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send(draft);
            }
          }}
          placeholder="Ask FinAlly, or say “buy 10 AAPL”"
          className="min-w-0 flex-1 resize-none border border-line bg-bg px-2 py-1.5 text-[13px] text-fg placeholder:text-faint focus:border-blue focus:outline-none"
        />
        <button
          type="submit"
          data-testid="chat-send"
          disabled={loading || !draft.trim()}
          className="self-stretch bg-purple px-3 text-[12px] font-semibold text-white hover:bg-purple-hi disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </aside>
  );
}
