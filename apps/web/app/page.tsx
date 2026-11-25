import Link from "next/link";
import RepoForm from "@/components/RepoForm";

const EXAMPLES = ["AkmalMohammed-1/QueueCTL", "pallets/click", "tiangolo/typer"];

export default function Home() {
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center px-4 py-16">
      <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Ask questions about any GitHub repo.</h1>
      <p className="mt-3 text-muted">
        Paste a public repository URL, or swap <code className="font-mono text-ink">github.com</code> for this
        site&apos;s domain in any repo link. Every answer links to the exact lines it came from.
      </p>

      <RepoForm />

      <div className="mt-10 rounded-lg border border-line bg-panel p-4 font-mono text-sm">
        <div className="text-muted">github.com/AkmalMohammed-1/QueueCTL</div>
        <div className="mt-1">
          <span className="text-accent">repochat</span>
          <span className="text-muted">…</span>/AkmalMohammed-1/QueueCTL
        </div>
      </div>

      <div className="mt-8 text-sm text-muted">
        Try:{" "}
        {EXAMPLES.map((e, i) => (
          <span key={e}>
            {i > 0 && " · "}
            <Link href={`/${e}`} className="font-mono text-ink underline decoration-line underline-offset-4 hover:decoration-accent">
              {e}
            </Link>
          </span>
        ))}
      </div>
    </div>
  );
}
