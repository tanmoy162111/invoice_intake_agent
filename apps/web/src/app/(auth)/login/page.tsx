import type { Metadata } from "next";

import { Brand } from "@/components/brand";
import { safeNext } from "@/lib/safe-redirect";

import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const next = safeNext(typeof sp.next === "string" ? sp.next : null);
  return (
    <main id="main" className="grid min-h-dvh lg:grid-cols-[1.05fr_1fr]">
      <section className="relative hidden overflow-hidden bg-primary p-12 text-primary-foreground lg:flex lg:flex-col lg:justify-between">
        <Brand inverted alwaysShowName />
        <div className="relative z-10 max-w-lg">
          <p className="display text-[3.6rem] font-medium leading-[1.02] tracking-tight">
            Exceptions,
            <br />
            <span className="italic">explained.</span>
          </p>
          <p className="mt-6 max-w-md text-[15px] leading-relaxed text-primary-foreground/70">
            Every invoice that needs a person arrives with what is wrong, the numbers behind it, and what to do
            next. Every decision is recorded under your name.
          </p>
        </div>
        <svg aria-hidden viewBox="0 0 400 400" className="absolute -bottom-24 -right-20 size-[26rem] rotate-[-14deg] text-brand opacity-90">
          <circle cx="200" cy="200" r="176" fill="none" stroke="currentColor" strokeWidth="7" />
          <circle cx="200" cy="200" r="150" fill="none" stroke="currentColor" strokeWidth="2" />
          <text x="200" y="222" textAnchor="middle" fontSize="64" fontFamily="var(--font-display)" fontStyle="italic" fill="currentColor">
            reviewed
          </text>
        </svg>
        <p className="relative z-10 text-xs uppercase tracking-[0.2em] text-primary-foreground/50">
          Demo build · synthetic data only
        </p>
      </section>
      <section className="flex flex-col justify-center px-6 py-10 sm:px-12">
        <div className="mx-auto w-full max-w-sm">
          <Brand className="mb-10 lg:hidden" alwaysShowName />
          <h1 className="display text-4xl font-medium leading-tight">Sign in</h1>
          <p className="mb-8 mt-2 text-sm text-muted-foreground">Reviewers only. There is no public access.</p>
          <LoginForm next={next} expired={sp.expired === "1"} />
        </div>
      </section>
    </main>
  );
}
