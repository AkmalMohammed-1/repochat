"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

/** Accepts github.com URLs, RepoChat URLs or plain owner/repo, and navigates to /owner/repo[/tree|blob/...]. */
export function toRepoPath(input: string): string | null {
  let text = input.trim();
  if (!text) return null;
  if (/^[a-z]+:\/\//i.test(text) || text.startsWith("github.com")) {
    try {
      text = new URL(text.includes("://") ? text : `https://${text}`).pathname;
    } catch {
      return null;
    }
  }
  const parts = text.split("/").filter(Boolean);
  if (parts.length < 2) return null;
  parts[1] = parts[1].replace(/\.git$/, "");
  if (!/^[A-Za-z0-9-]+$/.test(parts[0]) || !/^[A-Za-z0-9._-]+$/.test(parts[1])) return null;
  return "/" + parts.join("/");
}

export default function RepoForm() {
  const router = useRouter();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  return (
    <form
      className="mt-8"
      onSubmit={(e) => {
        e.preventDefault();
        const path = toRepoPath(value);
        if (!path) {
          setError("Enter a GitHub repository, e.g. https://github.com/owner/repo");
          return;
        }
        router.push(path);
      }}
    >
      <div className="flex gap-2">
        <input
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setError(null);
          }}
          placeholder="https://github.com/owner/repo"
          aria-label="GitHub repository URL"
          className="min-w-0 flex-1 rounded-md border border-line bg-panel px-3 py-2.5 font-mono text-sm outline-none focus:border-accent"
        />
        <button
          type="submit"
          className="rounded-md bg-accent px-4 py-2.5 text-sm font-medium text-white hover:opacity-90 dark:text-black"
        >
          Open
        </button>
      </div>
      {error && <p className="mt-2 text-sm text-danger">{error}</p>}
    </form>
  );
}
