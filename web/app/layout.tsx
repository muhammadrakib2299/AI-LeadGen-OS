import type { Metadata } from "next";
import Link from "next/link";
import { AuthGate } from "@/components/AuthGate";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI LeadGen OS",
  description: "Compliant lead generation dashboard (EU/UK)",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div className="mx-auto max-w-5xl px-6 py-8">
          <header className="mb-8 flex items-baseline justify-between gap-4">
            <div className="flex items-baseline gap-6">
              <Link
                href="/"
                className="text-2xl font-semibold tracking-tight hover:text-blue-600 dark:hover:text-blue-400"
              >
                AI LeadGen OS
              </Link>
              <nav className="flex items-baseline gap-4 text-sm">
                <Link
                  href="/"
                  className="text-neutral-600 hover:text-blue-600 dark:text-neutral-300 dark:hover:text-blue-400"
                >
                  Jobs
                </Link>
                <Link
                  href="/bulk"
                  className="text-neutral-600 hover:text-blue-600 dark:text-neutral-300 dark:hover:text-blue-400"
                >
                  Bulk upload
                </Link>
                <Link
                  href="/review"
                  className="text-neutral-600 hover:text-blue-600 dark:text-neutral-300 dark:hover:text-blue-400"
                >
                  Review
                </Link>
                <Link
                  href="/blacklist"
                  className="text-neutral-600 hover:text-blue-600 dark:text-neutral-300 dark:hover:text-blue-400"
                >
                  Blacklist
                </Link>
                <Link
                  href="/api-keys"
                  className="text-neutral-600 hover:text-blue-600 dark:text-neutral-300 dark:hover:text-blue-400"
                >
                  API keys
                </Link>
              </nav>
            </div>
            <span className="text-xs text-neutral-500 dark:text-neutral-400">
              EU/UK · GDPR-compliant
            </span>
          </header>
          <main>
            <AuthGate>{children}</AuthGate>
          </main>
        </div>
      </body>
    </html>
  );
}
