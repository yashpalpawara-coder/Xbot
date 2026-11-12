"use client";

import { useEffect, useState } from "react";

import { PostCard } from "@/components/post-card";
import { request, when } from "@/lib/api";
import type { SavedItem } from "@/lib/types";

export default function SavedPage() {
  const [items, setItems] = useState<SavedItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    const body = await request<{ items: SavedItem[] }>("/api/saved");
    setItems(body.items);
  }

  useEffect(() => {
    void load().catch((exc: Error) => setError(exc.message));
  }, []);

  async function remove(id: string) {
    await request(`/api/saved/${id}`, { method: "DELETE" });
    await load();
  }

  return (
    <div className="space-y-6">
      <header>
        <p className="text-sm uppercase tracking-[0.16em] text-[var(--gold)]">Library</p>
        <h1 className="mt-2 font-[family-name:var(--font-serif)] text-5xl font-medium tracking-tight">Saved work.</h1>
      </header>
      {error ? <p className="text-[var(--bad)]">{error}</p> : null}
      {items.length === 0 ? <p className="text-[var(--muted)]">Nothing saved yet. Save a post from Finder.</p> : null}
      <div className="space-y-3">
        {items.map((item) => (
          <div key={item.id} className="space-y-2">
            <div className="flex items-center justify-between text-sm text-[var(--muted)]">
              <span>{item.item_type} · {when(item.created_at)}</span>
              <button className="btn-ghost" type="button" onClick={() => remove(item.id)}>Remove</button>
            </div>
            {item.post ? <PostCard post={item.post} /> : <p className="panel p-4">Saved {item.item_type} {item.item_id}</p>}
          </div>
        ))}
      </div>
    </div>
  );
}
