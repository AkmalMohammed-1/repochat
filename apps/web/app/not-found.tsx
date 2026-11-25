import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-lg px-4 py-24 text-center">
      <h1 className="text-xl font-semibold">That isn&apos;t a repository page</h1>
      <p className="mt-2 text-muted">
        RepoChat URLs look like <code className="font-mono">/owner/repo</code>,{" "}
        <code className="font-mono">/owner/repo/tree/branch</code> or{" "}
        <code className="font-mono">/owner/repo/blob/branch/path</code>.
      </p>
      <Link href="/" className="mt-6 inline-block text-accent underline underline-offset-4">
        Back to the start page
      </Link>
    </div>
  );
}
