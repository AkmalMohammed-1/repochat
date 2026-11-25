"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import Answer from "@/components/Answer";
import SourcePanel, { type Selection } from "@/components/SourcePanel";
import {
  ApiError,
  type ChatTurn,
  type Citation,
  getSuggestions,
  resolveRepo,
  type Snapshot,
  type Source,
  streamChat,
  watchProgress,
} from "@/lib/api";
import { type CiteTarget, githubLineUrl } from "@/lib/citations";

interface Message {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  citations?: Citation[];
  pending?: boolean;
  error?: string;
}

type Phase =
  | { kind: "resolving" }
  | { kind: "indexing"; snap: Snapshot }
  | { kind: "ready"; snap: Snapshot }
  | { kind: "error"; code: string; message: string };

interface Props {
  owner: string;
  repo: string;
  branch?: string;
  path?: string;
}

export default function RepoChat({ owner, repo, branch, path }: Props) {
  const [phase, setPhase] = useState<Phase>({ kind: "resolving" });

  useEffect(() => {
    let stop: (() => void) | undefined;
    let cancelled = false;
    resolveRepo(owner, repo, branch, path)
      .then((snap) => {
        if (cancelled) return;
        const focus = snap.focus_path;
        const onSnap = (s: Snapshot) => {
          const merged = { ...s, branch: snap.branch, focus_path: focus };
          if (s.status === "ready") setPhase({ kind: "ready", snap: merged });
          else if (s.status === "failed" || s.status === "rejected")
            setPhase({ kind: "error", code: s.status, message: s.error ?? "Indexing failed." });
          else setPhase({ kind: "indexing", snap: merged });
        };
        onSnap(snap);
        if (snap.status !== "ready" && snap.status !== "rejected") {
          stop = watchProgress(snap.snapshot_id, onSnap, () =>
            setPhase({ kind: "error", code: "progress_lost", message: "Lost connection while indexing. Reload to retry." }),
          );
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        const err = e instanceof ApiError ? e : new ApiError("error", "Something went wrong.", 0);
        setPhase({ kind: "error", code: err.code, message: err.message });
      });
    return () => {
      cancelled = true;
      stop?.();
    };
  }, [owner, repo, branch, path]);

  if (phase.kind === "error") return <ErrorView owner={owner} repo={repo} code={phase.code} message={phase.message} />;
  if (phase.kind === "resolving")
    return <Centered title={`${owner}/${repo}`} subtitle="Looking up the latest commit…" />;
  if (phase.kind === "indexing") return <Indexing snap={phase.snap} />;
  return <Chat snap={phase.snap} />;
}

function Centered({ title, subtitle, children }: { title: string; subtitle: string; children?: React.ReactNode }) {
  return (
    <div className="mx-auto w-full max-w-md flex-1 px-4 py-24">
      <h1 className="font-mono text-lg font-semibold">{title}</h1>
      <p className="mt-1 text-sm text-muted">{subtitle}</p>
      {children}
    </div>
  );
}

function Indexing({ snap }: { snap: Snapshot }) {
  const pct = snap.files_total ? Math.round((100 * snap.files_done) / snap.files_total) : 0;
  const label =
    snap.status === "queued"
      ? "Waiting for an indexing worker…"
      : snap.files_total
        ? `Indexing ${snap.files_done} of ${snap.files_total} files`
        : "Downloading and scanning files…";
  return (
    <Centered title={`${snap.owner}/${snap.repo}`} subtitle={`First visit to commit ${snap.sha.slice(0, 7)}: indexing it once.`}>
      <div className="mt-8">
        <div className="flex justify-between text-sm">
          <span>{label}</span>
          {snap.files_total > 0 && <span className="font-mono text-muted">{pct}%</span>}
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-line">
          <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${pct}%` }} />
        </div>
        <p className="mt-3 font-mono text-xs text-muted">{snap.chunks_done.toLocaleString()} chunks embedded</p>
        {snap.error && <p className="mt-3 text-xs text-muted">{snap.error}</p>}
      </div>
    </Centered>
  );
}

const ERROR_TITLES: Record<string, string> = {
  not_found: "Repository not found",
  private: "Private repository",
  too_large: "Repository too large",
  rejected: "Can't index this repository",
  failed: "Indexing failed",
  ref_not_found: "Branch not found",
  github_rate_limited: "GitHub rate limit reached",
  rate_limited: "Slow down a little",
  invalid_url: "Not a repository URL",
  api_unreachable: "RepoChat API unreachable",
};

function ErrorView({ owner, repo, code, message }: { owner: string; repo: string; code: string; message: string }) {
  return (
    <Centered title={ERROR_TITLES[code] ?? "Something went wrong"} subtitle={`${owner}/${repo}`}>
      <p className="mt-6">{message}</p>
      <div className="mt-6 flex gap-4 text-sm">
        <Link href="/" className="text-accent underline underline-offset-4">
          Try another repository
        </Link>
        <a href={`https://github.com/${owner}/${repo}`} className="text-muted underline underline-offset-4" target="_blank" rel="noreferrer">
          Open on GitHub
        </a>
      </div>
    </Centered>
  );
}

function Chat({ snap }: { snap: Snapshot }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [selection, setSelection] = useState<Selection | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getSuggestions(snap.snapshot_id).then(setSuggestions);
    return () => abortRef.current?.abort();
  }, [snap.snapshot_id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  const updateLast = (fn: (m: Message) => Message) =>
    setMessages((prev) => [...prev.slice(0, -1), fn(prev[prev.length - 1])]);

  const ask = useCallback(
    async (question: string) => {
      const q = question.trim();
      if (!q || busy) return;
      const history: ChatTurn[] = messages
        .filter((m) => !m.error && !m.pending)
        .map((m) => ({ role: m.role, content: m.content }));
      setMessages((prev) => [...prev, { role: "user", content: q }, { role: "assistant", content: "", pending: true }]);
      setInput("");
      setBusy(true);
      const controller = new AbortController();
      abortRef.current = controller;
      await streamChat(
        { snapshot_id: snap.snapshot_id, question: q, history, focus_path: snap.focus_path },
        {
          onSources: (sources) => updateLast((m) => ({ ...m, sources })),
          onToken: (text) => updateLast((m) => ({ ...m, content: m.content + text })),
          onDone: (citations) => updateLast((m) => ({ ...m, citations, pending: false })),
          onError: (error) => updateLast((m) => ({ ...m, error, pending: false })),
        },
        controller.signal,
      );
      updateLast((m) => ({ ...m, pending: false }));
      setBusy(false);
    },
    [busy, messages, snap.snapshot_id, snap.focus_path],
  );

  const openCite = (target: CiteTarget, sources: Source[] = []) => {
    const source =
      sources.find((s) => s.path === target.path && target.start <= s.end_line && s.start_line <= target.end) ??
      sources.find((s) => s.path === target.path);
    if (source) setSelection({ source, start: target.start, end: target.end });
    else window.open(githubLineUrl(snap.owner, snap.repo, snap.sha, target), "_blank", "noopener");
  };

  return (
    <div className="flex flex-1">
      <section className="flex h-[calc(100dvh-3rem)] min-w-0 flex-1 flex-col">
        <div className="border-b border-line px-4 py-2.5">
          <div className="mx-auto flex max-w-3xl flex-wrap items-baseline gap-x-3 gap-y-1">
            <a href={`https://github.com/${snap.owner}/${snap.repo}`} target="_blank" rel="noreferrer" className="font-mono text-sm font-semibold hover:underline">
              {snap.owner}/{snap.repo}
            </a>
            <span className="font-mono text-xs text-muted">
              {snap.branch ? `${snap.branch} · ` : ""}
              {snap.sha.slice(0, 7)} · {snap.files_total} files · {snap.chunks_done.toLocaleString()} chunks
            </span>
            {snap.focus_path && <span className="font-mono text-xs text-accent">focused on {snap.focus_path}</span>}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-4">
          <div className="mx-auto max-w-3xl py-6">
            {messages.length === 0 && (
              <div className="py-10">
                <p className="text-muted">Ask anything about this codebase. Suggestions:</p>
                <div className="mt-4 flex flex-wrap gap-2">
                  {suggestions.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => ask(s)}
                      className="rounded-full border border-line bg-panel px-3 py-1.5 text-sm hover:border-accent"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="mt-6 flex justify-end">
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-accent-soft px-3.5 py-2 text-[15px]">{m.content}</div>
                </div>
              ) : (
                <div key={i} className="mt-4">
                  {m.content && <Answer text={m.content} onCite={(t) => openCite(t, m.sources)} />}
                  {m.pending && !m.content && (
                    <p className="text-sm text-muted">{m.sources ? "Writing answer…" : "Searching the code…"}</p>
                  )}
                  {m.error && <p className="mt-2 text-sm text-danger">{m.error}</p>}
                  {m.sources && m.sources.length > 0 && !m.pending && (
                    <details className="mt-3 text-sm">
                      <summary className="cursor-pointer text-muted">{m.sources.length} retrieved chunks</summary>
                      <ul className="mt-2 space-y-1">
                        {m.sources.map((s) => (
                          <li key={s.id}>
                            <button
                              type="button"
                              className="font-mono text-xs hover:text-accent"
                              onClick={() => setSelection({ source: s, start: s.start_line, end: s.end_line })}
                            >
                              {s.path}:L{s.start_line}-L{s.end_line}
                              {s.symbol_name ? ` · ${s.symbol_name}` : ""}
                            </button>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
              ),
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        <form
          className="border-t border-line px-4 py-3"
          onSubmit={(e) => {
            e.preventDefault();
            ask(input);
          }}
        >
          <div className="mx-auto flex max-w-3xl gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  ask(input);
                }
              }}
              rows={1}
              placeholder={`Ask about ${snap.repo}…`}
              aria-label="Question"
              className="max-h-40 min-w-0 flex-1 resize-none rounded-md border border-line bg-panel px-3 py-2.5 text-[15px] outline-none focus:border-accent"
            />
            {busy ? (
              <button type="button" onClick={() => abortRef.current?.abort()} className="rounded-md border border-line px-4 text-sm">
                Stop
              </button>
            ) : (
              <button type="submit" disabled={!input.trim()} className="rounded-md bg-accent px-4 text-sm font-medium text-white disabled:opacity-40 dark:text-black">
                Ask
              </button>
            )}
          </div>
        </form>
      </section>
      {selection && <SourcePanel selection={selection} onClose={() => setSelection(null)} />}
    </div>
  );
}
