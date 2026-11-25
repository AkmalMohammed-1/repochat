import type { Metadata } from "next";
import { notFound } from "next/navigation";
import RepoChat from "@/components/RepoChat";

type Params = { owner: string; repo: string; rest?: string[] };

/** /{owner}/{repo}, /{owner}/{repo}/tree/{branch...}, /{owner}/{repo}/blob/{branch}/{path...} */
function parseRest(rest: string[] = []): { branch?: string; path?: string } | null {
  if (rest.length === 0) return {};
  if (rest[0] === "tree" && rest.length >= 2) return { branch: rest.slice(1).join("/") };
  if (rest[0] === "blob" && rest.length >= 3) return { branch: rest[1], path: rest.slice(2).join("/") };
  return null;
}

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { owner, repo } = await params;
  return { title: `${owner}/${repo} · RepoChat` };
}

export default async function RepoPage({ params }: { params: Promise<Params> }) {
  const { owner, repo, rest } = await params;
  const parsed = parseRest(rest?.map(decodeURIComponent));
  if (!parsed) notFound();
  return (
    <RepoChat
      owner={decodeURIComponent(owner)}
      repo={decodeURIComponent(repo)}
      branch={parsed.branch}
      path={parsed.path}
    />
  );
}
