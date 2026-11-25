import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "RepoChat",
  description: "Chat with any public GitHub repository. Answers cite the exact lines of code.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable} font-sans antialiased`}>
        <div className="flex min-h-dvh flex-col">
          <header className="border-b border-line">
            <div className="mx-auto flex h-12 max-w-6xl items-center justify-between px-4">
              <Link href="/" className="font-mono text-sm font-semibold tracking-tight">
                repochat
              </Link>
              <a
                href="https://github.com/AkmalMohammed-1"
                className="text-sm text-muted hover:text-ink"
                target="_blank"
                rel="noreferrer"
              >
                GitHub
              </a>
            </div>
          </header>
          <main className="flex flex-1 flex-col">{children}</main>
        </div>
      </body>
    </html>
  );
}
