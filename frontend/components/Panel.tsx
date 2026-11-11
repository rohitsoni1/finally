import type { ReactNode } from "react";

interface PanelProps {
  title: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  testId?: string;
}

export function Panel({ title, right, children, className = "", bodyClassName = "", testId }: PanelProps) {
  return (
    <section
      data-testid={testId}
      className={`flex min-h-0 min-w-0 flex-col border border-line bg-surface ${className}`}
    >
      <header className="flex h-8 shrink-0 items-center justify-between gap-3 border-b border-line px-3">
        <h2 className="truncate text-[12px] font-semibold text-fg">{title}</h2>
        {right ? <div className="flex items-center gap-2 text-[11px] text-muted">{right}</div> : null}
      </header>
      <div className={`relative min-h-0 flex-1 ${bodyClassName}`}>{children}</div>
    </section>
  );
}
