import type { Metadata } from "next";
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
          <header className="mb-8 flex items-baseline justify-between">
            <h1 className="text-2xl font-semibold tracking-tight">
              AI LeadGen OS
            </h1>
            <span className="text-xs text-neutral-500 dark:text-neutral-400">
              EU/UK · GDPR-compliant
            </span>
          </header>
          <main>{children}</main>
        </div>
      </body>
    </html>
  );
}
