"use client";

import Link from "next/link";
import { useState } from "react";

import { metric, request, when } from "@/lib/api";
import type { Post } from "@/lib/types";

export function PostCard({ post }: { post: Post }) {
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setError(null);
    try {
      await request("/api/saved", {
        method: "POST",
        body: JSON.stringify({ item_type: "post", item_id: post.id, tags: [] }),
      });
      setSaved(true);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not save");
    }
  }

  return (
    <article className="panel p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="font-medium">{post.author || "Unknown author"}</div>
          <div className="text-sm text-[var(--muted)]">@{post.handle || "unknown"}</div>
        </div>
        <div className="text-right text-sm text-[var(--muted)]">
          <div>{post.relevance ?? "—"} relevance</div>
          <div>{when(post.created_at)}</div>
        </div>
      </div>
      <p className="mt-4 whitespace-pre-wrap leading-7">{post.text}</p>
      <div className="mt-4 flex flex-wrap gap-3 text-sm text-[var(--muted)]">
        <span>{metric(post.latest_views)} views</span>
        <span>{metric(post.latest_likes)} likes</span>
        <span>{metric(post.latest_replies)} replies</span>
        <span>{metric(post.latest_reposts)} reposts</span>
      </div>
      <div className="mt-5 flex flex-wrap gap-2">
        <Link className="btn-ghost" href={`/posts/${post.id}`}>Open</Link>
        <Link className="btn-ghost" href={`/studio/${post.id}`}>Comment studio</Link>
        <a className="btn-ghost" href={post.url} target="_blank" rel="noreferrer">On X</a>
        <button className="btn" type="button" onClick={save} disabled={saved}>{saved ? "Saved" : "Save"}</button>
      </div>
      {error ? <p className="mt-3 text-sm text-[var(--bad)]">{error}</p> : null}
    </article>
  );
}

export function JobLog({ message, progress, logs, error }: { message?: string; progress?: number; logs?: string[]; error?: string | null }) {
  return (
    <section className="panel p-4">
      <div className="mb-3 flex items-center justify-between gap-3 text-sm">
        <span>{message || "Working"}</span>
        <span className="text-[var(--muted)]">{progress ?? 0}%</span>
      </div>
      <div className="progress"><span style={{ width: `${progress ?? 0}%` }} /></div>
      {error ? <p className="mt-3 text-sm text-[var(--bad)]">{error}</p> : null}
      {logs && logs.length > 0 ? (
        <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap text-xs text-[var(--muted)]">{logs.join("\n")}</pre>
      ) : null}
    </section>
  );
}
