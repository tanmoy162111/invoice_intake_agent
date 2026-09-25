"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ListChecks, Upload } from "lucide-react";

import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/queue", label: "Queue", icon: ListChecks, match: (p: string) => p.startsWith("/queue") || p.startsWith("/invoices") },
  { href: "/upload", label: "Upload", icon: Upload, match: (p: string) => p.startsWith("/upload") },
];

export function NavLinks() {
  const pathname = usePathname();
  return (
    <nav aria-label="Main" className="flex items-center gap-1">
      {LINKS.map(({ href, label, icon: Icon, match }) => {
        const active = match(pathname);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            aria-label={label}
            className={cn(
              "inline-flex h-10 items-center gap-2 rounded-md px-2.5 text-sm font-medium transition-colors hover:bg-muted sm:px-3",
              active ? "text-foreground" : "text-muted-foreground",
              active && "bg-muted",
            )}
          >
            <Icon className="size-4" aria-hidden />
            <span className="hidden sm:inline">{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
