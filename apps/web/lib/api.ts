// Typed client for the RepoChat API.

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

export type SnapshotStatus = "queued" | "running" | "ready" | "failed" | "rejected";

export interface Snapshot {
  snapshot_id: number;
  owner: string;
  repo: string;
  sha: string;
  branch: string | null;
  focus_path: string | null;
  status: SnapshotStatus;
  error: string | null;
  files_total: number;
  files_done: number;
  chunks_done: number;
}

export interface Source {
  id: number;
  path: string;
  symbol_name: string | null;
  kind: string;
  language: string;
  start_line: number;
  end_line: number;
  content: string;
  url: string;
}

export interface Citation {
  raw: string;
  path: string;
  start: number;
  end: number;
  url: string;
  valid: boolean;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function readError(resp: Response): Promise<ApiError> {
  try {
    const body = await resp.json();
    const err = body.error ?? body.detail ?? {};
    return new ApiError(err.code ?? "error", err.message ?? resp.statusText, resp.status);
  } catch {
    return new ApiError("error", `Request failed (${resp.status})`, resp.status);
  }
}

export async function resolveRepo(owner: string, repo: string, branch?: string, path?: string): Promise<Snapshot> {
  let url = `/${owner}/${repo}`;
  if (branch && path) url += `/blob/${branch}/${path}`;
  else if (branch) url += `/tree/${branch}`;
  let resp: Response;
  try {
    resp = await fetch(`${API_URL}/api/repos/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
  } catch {
    throw new ApiError("api_unreachable", "Can't reach the RepoChat API. Is it running?", 0);
  }
  if (!resp.ok) throw await readError(resp);
  return resp.json();
}

export async function getSuggestions(snapshotId: number): Promise<string[]> {
  const resp = await fetch(`${API_URL}/api/snapshots/${snapshotId}/suggestions`);
  if (!resp.ok) return [];
  return (await resp.json()).questions ?? [];
}

export function watchProgress(
  snapshotId: number,
  onUpdate: (s: Snapshot) => void,
  onError: () => void,
): () => void {
  const source = new EventSource(`${API_URL}/api/snapshots/${snapshotId}/events`);
  source.addEventListener("progress", (e) => {
    const snap: Snapshot = JSON.parse((e as MessageEvent).data);
    onUpdate(snap);
    if (["ready", "failed", "rejected"].includes(snap.status)) source.close();
  });
  source.onerror = () => {
    // EventSource reconnects by itself; only give up if the connection is closed for good.
    if (source.readyState === EventSource.CLOSED) onError();
  };
  return () => source.close();
}

export interface ChatHandlers {
  onSources: (sources: Source[]) => void;
  onToken: (text: string) => void;
  onDone: (citations: Citation[]) => void;
  onError: (message: string) => void;
}

/** POST /api/chat and parse the Server-Sent Events stream (EventSource can't POST, so we read it by hand). */
export async function streamChat(
  body: { snapshot_id: number; question: string; history: ChatTurn[]; focus_path?: string | null },
  handlers: ChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(`${API_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch {
    if (!signal?.aborted) handlers.onError("Can't reach the RepoChat API.");
    return;
  }
  if (!resp.ok || !resp.body) {
    handlers.onError((await readError(resp)).message);
    return;
  }
  try {
    for await (const { event, data } of parseSSE(resp.body)) {
      const payload = JSON.parse(data);
      if (event === "sources") handlers.onSources(payload.chunks);
      else if (event === "token") handlers.onToken(payload.text);
      else if (event === "done") handlers.onDone(payload.citations);
      else if (event === "error") handlers.onError(payload.message);
    }
  } catch {
    // Aborted by the user (Stop button) or the connection dropped mid-answer.
    if (!signal?.aborted) handlers.onError("The connection was interrupted.");
  }
}

export async function* parseSSE(stream: ReadableStream<Uint8Array>): AsyncGenerator<{ event: string; data: string }> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) yield { event, data: data.join("\n") };
    }
  }
}
