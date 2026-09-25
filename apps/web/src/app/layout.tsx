import type { Metadata, Viewport } from "next";
import { cookies, headers } from "next/headers";

import { StyleNonce } from "@/components/nonce";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Invoice Intake", template: "%s · Invoice Intake" },
  description: "Review the invoices that need a person: every problem explained, every decision recorded.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f5f1e8" },
    { media: "(prefers-color-scheme: dark)", color: "#0f131a" },
  ],
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const theme = (await cookies()).get("theme")?.value;
  const nonce = (await headers()).get("x-nonce");
  const cls = theme === "dark" ? "dark" : theme === "light" ? "light" : "";
  return (
    <html lang="en" className={cls} suppressHydrationWarning>
      <body>
        <StyleNonce nonce={nonce} />
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[100] focus:rounded-md focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground"
        >
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
