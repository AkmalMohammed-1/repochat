// Turn `[path:Lstart-Lend]` citations in an answer into Markdown links with a `cite:` URL,
// which the chat renderer shows as a chip that opens the source panel.

// Same pattern as apps/api/graph/citations.py: [path:L1-L2], `path:L1-L2` or 【path:L1-L2】.
// Optional outer backticks (`[path:L1-L2]`) are consumed so the result renders as a link, not as code.
const CITATION = /`?[\[`【]([^\[\]`【】\s:][^\[\]`【】:]*?):L(\d+)(?:\s*-\s*L?(\d+))?[\]`】]`?(?!\()/g;

export interface CiteTarget {
  path: string;
  start: number;
  end: number;
}

export function linkCitations(markdown: string): string {
  return markdown.replace(CITATION, (_m, path: string, start: string, end?: string) => {
    const p = path.trim().replace(/^\.?\//, "");
    const s = Number(start);
    const e = Number(end ?? start);
    const label = s === e ? `${p}:L${s}` : `${p}:L${s}-L${e}`;
    return `[${label}](cite:${encodeURIComponent(p)}:${Math.min(s, e)}:${Math.max(s, e)})`;
  });
}

export function parseCiteUrl(href: string): CiteTarget | null {
  const m = /^cite:([^:]+):(\d+):(\d+)$/.exec(href);
  if (!m) return null;
  return { path: decodeURIComponent(m[1]), start: Number(m[2]), end: Number(m[3]) };
}

export function githubLineUrl(owner: string, repo: string, sha: string, t: CiteTarget): string {
  const anchor = t.start === t.end ? `#L${t.start}` : `#L${t.start}-L${t.end}`;
  return `https://github.com/${owner}/${repo}/blob/${sha}/${t.path}${anchor}`;
}

/** Only allow links we generate or plain web links; drops javascript: and other schemes from model output. */
export function safeUrl(url: string): string {
  return /^(https?:|cite:|#)/i.test(url) ? url : "";
}
