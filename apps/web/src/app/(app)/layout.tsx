import Link from "next/link";

import { Brand } from "@/components/brand";
import { NavLinks } from "@/components/nav-links";
import { ThemeToggle } from "@/components/theme-toggle";
import { UserMenu } from "@/components/user-menu";
import { getMe } from "@/lib/api/server";

import { logoutAction } from "../(auth)/login/actions";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const me = await getMe();
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-40 border-b bg-background/85 backdrop-blur supports-[backdrop-filter]:bg-background/70">
        <div className="mx-auto flex h-16 max-w-[88rem] items-center justify-between gap-3 px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-2 sm:gap-8">
            <Link href="/queue" aria-label="Invoice Intake, home">
              <Brand />
            </Link>
            <NavLinks />
          </div>
          <div className="flex items-center gap-1">
            <ThemeToggle />
            <UserMenu user={me.user ?? "reviewer"} signOut={logoutAction} />
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-[88rem] flex-1 px-4 py-8 sm:px-6">
        {children}
      </main>
      <footer className="border-t py-5 text-center text-xs text-muted-foreground">
        Demo build · synthetic data · every decision is recorded
      </footer>
    </div>
  );
}
