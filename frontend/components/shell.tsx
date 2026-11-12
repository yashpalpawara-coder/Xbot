"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  ["/", "Overview"],
  ["/finder", "Finder"],
  ["/coming", "Coming"],
  ["/saved", "Library"],
  ["/analytics", "Analysis"],
  ["/settings", "Settings"],
];

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">XBot</div>
        <p className="mt-3 text-sm text-[var(--muted)]">Find a post. Read it. Reply once, after you approve it.</p>
        <nav className="nav">
          {links.map(([href, label]) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link key={href} href={href} className={active ? "active" : ""}>
                {label}
              </Link>
            );
          })}
        </nav>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}
