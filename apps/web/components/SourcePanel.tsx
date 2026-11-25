"use client";

import { useEffect, useRef } from "react";
import type { Source } from "@/lib/api";

export interface Selection {
  source: Source;
  start: number;
  end: number;
}

/** Shows a retrieved chunk with line numbers, highlighting the cited lines. */
export default function SourcePanel({ selection, onClose }: { selection: Selection; onClose: () => void }) {
  const { source, start, end } = selection;
  const lines = source.content.split("\n");
  const firstHighlighted = useRef<HTMLDivElement>(null);

  useEffect(() => {
    firstHighlighted.current?.scrollIntoView({ block: "center" });
  }, [selection]);

  return (
    <aside className="fixed inset-0 z-20 flex flex-col bg-panel lg:static lg:z-auto lg:h-[calc(100dvh-3rem)] lg:w-[46%] lg:border-l lg:border-line">
      <div className="flex items-center gap-3 border-b border-line px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-sm">{source.path}</div>
          <div className="text-xs text-muted">
            {source.symbol_name ? `${source.symbol_name} · ` : ""}
            lines {start}–{end}
          </div>
        </div>
        <a href={source.url} target="_blank" rel="noreferrer" className="text-sm text-accent hover:underline">
          GitHub ↗
        </a>
        <button type="button" onClick={onClose} className="text-sm text-muted hover:text-ink" aria-label="Close">
          ✕
        </button>
      </div>
      <div className="flex-1 overflow-auto bg-code py-2 font-mono text-[0.8rem] leading-5">
        {lines.map((line, i) => {
          const n = source.start_line + i;
          const hit = n >= start && n <= end;
          return (
            <div
              key={n}
              ref={hit && n === Math.max(start, source.start_line) ? firstHighlighted : undefined}
              className={`flex ${hit ? "bg-accent-soft" : ""}`}
            >
              <span className="w-12 shrink-0 select-none pr-3 text-right text-muted">{n}</span>
              <pre className="whitespace-pre pr-4">{line || " "}</pre>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
