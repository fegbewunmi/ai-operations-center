import type { Metadata } from "next";
import Link from "next/link";
import "reactflow/dist/style.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Trace — AI Operations Center",
  description: "Multi-agent incident investigation console",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full">
      <body className="h-full flex flex-col overflow-hidden">
        <header className="flex items-center gap-6 border-b border-border bg-surface px-4 h-11 shrink-0">
          <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight text-fg">
            <span className="inline-block h-2 w-2 rounded-full bg-accent" />
            Trace
          </Link>
          <nav className="flex items-center gap-4 text-[13px]">
            <Link href="/" className="text-fg-muted hover:text-fg transition-colors">
              Incident Library
            </Link>
            <Link href="/evaluation" className="text-fg-muted hover:text-fg transition-colors">
              Evaluation
            </Link>
          </nav>
        </header>
        <main className="flex-1 min-h-0 flex flex-col overflow-y-auto overflow-x-hidden">{children}</main>
      </body>
    </html>
  );
}
