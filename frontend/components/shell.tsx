"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { request } from "@/lib/api";
import type { Job, ProductChoice } from "@/lib/types";
import { useJob } from "@/lib/use-job";

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
  const [products, setProducts] = useState<ProductChoice[]>([]);
  const [selected, setSelected] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [failed, setFailed] = useState(false);
  const job = useJob(jobId);
  const locked = job?.status === "running";
  const shown = products.some((product) => product.id === selected) ? selected : products[0]?.id || "";

  async function load() {
    const body = await request<{ products: ProductChoice[]; selected_id: string }>("/api/products");
    setProducts(body.products || []);
    setSelected(body.selected_id || "");
  }

  useEffect(() => {
    void load().catch(() => undefined);
    function onProduct() {
      void load().catch(() => undefined);
    }
    window.addEventListener("xbot-product", onProduct);
    return () => window.removeEventListener("xbot-product", onProduct);
  }, [path]);

  useEffect(() => {
    if (!job) return;
    setNote(job.error || job.message || "");
    setFailed(job.status === "error");
    if (job.status === "done" || job.status === "error" || job.status === "stopped") {
      void load().catch(() => undefined);
      window.dispatchEvent(new Event("xbot-product"));
    }
  }, [job]);

  async function choose(id: string) {
    if (!id || id === selected || locked) return;
    setSelected(id);
    setFailed(false);
    setNote("Switching the X account…");
    try {
      const started = await request<Job>("/api/products/select", {
        method: "POST",
        body: JSON.stringify({ id }),
      });
      setJobId(started.id);
      window.dispatchEvent(new Event("xbot-product"));
    } catch (exc) {
      setFailed(true);
      setNote(exc instanceof Error ? exc.message : "Could not switch products.");
      void load().catch(() => undefined);
    }
  }

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">XBot</div>
        <p className="mt-3 text-sm text-[var(--muted)]">Find a post. Read it. Reply once, after you approve it.</p>
        <label className="product-pick">
          <span className="label">Product</span>
          <select className="select" value={shown} disabled={locked || products.length === 0} onChange={(event) => void choose(event.target.value)}>
            {products.length === 0 ? <option value="">No product saved</option> : null}
            {products.map((product) => (
              <option key={product.id} value={product.id}>
                {product.name}
                {product.x_handle ? ` · @${product.x_handle.replace(/^@/, "")}` : ""}
              </option>
            ))}
          </select>
          {products.length === 0 ? <p className="product-note">Verify a product on Analysis. It shows up here.</p> : null}
          {note ? <p className={failed ? "product-note bad" : "product-note"}>{note}</p> : null}
        </label>
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
