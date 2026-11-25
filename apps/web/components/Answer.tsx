"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CiteTarget, linkCitations, parseCiteUrl, safeUrl } from "@/lib/citations";

/** Renders an assistant answer. Citations become chips that open the source panel. */
export default function Answer({ text, onCite }: { text: string; onCite: (t: CiteTarget) => void }) {
  return (
    <div className="answer text-[15px]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={safeUrl}
        components={{
          a: ({ href, children }) => {
            const cite = href ? parseCiteUrl(href) : null;
            if (cite) {
              return (
                <button
                  type="button"
                  onClick={() => onCite(cite)}
                  className="cite mx-0.5 inline-flex items-center rounded bg-accent-soft px-1.5 py-px align-baseline font-mono text-[0.75rem] text-accent hover:underline"
                >
                  {children}
                </button>
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer noopener">
                {children}
              </a>
            );
          },
        }}
      >
        {linkCitations(text)}
      </ReactMarkdown>
    </div>
  );
}
