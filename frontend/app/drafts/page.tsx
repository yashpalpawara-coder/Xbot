"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { JobLog } from "@/components/post-card";
import { request } from "@/lib/api";
import type { ComingPost, Job } from "@/lib/types";
import { useJob } from "@/lib/use-job";

export default function DraftsPage() {
  const [posts, setPosts] = useState<ComingPost[]>([]);
  const [productName, setProductName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const drafts = posts.filter((post) => post.status === "drafted" && (post.reply || "").trim());
  const running = job?.status === "running";

  async function load() {
    const body = await request<{ posts: ComingPost[]; product_name?: string }>("/api/finder/coming");
    setPosts(body.posts || []);
    setProductName(body.product_name || "");
  }

  useEffect(() => {
    void load().catch((exc: Error) => setError(exc.message));
    function onProduct() {
      void load().catch((exc: Error) => setError(exc.message));
    }
    window.addEventListener("xbot-product", onProduct);
    return () => window.removeEventListener("xbot-product", onProduct);
  }, []);

  useEffect(() => {
    if (!job) return;
    if (job.status === "running") {
      const timer = window.setInterval(() => {
        void request<{ posts: ComingPost[]; product_name?: string }>("/api/finder/coming")
          .then((body) => {
            setPosts(body.posts || []);
            setProductName(body.product_name || "");
          })
          .catch(() => undefined);
      }, 2000);
      return () => window.clearInterval(timer);
    }
    if (job.status === "done" || job.status === "error" || job.status === "stopped") {
      window.dispatchEvent(new Event("xbot-product"));
    }
    void load().catch((exc: Error) => setError(exc.message));
  }, [job]);

  async function postDrafts() {
    setError(null);
    try {
      const started = await request<Job>("/api/coming/post", { method: "POST" });
      setJobId(started.id);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not post the replies.");
    }
  }

  async function stop() {
    if (!jobId) return;
    await request(`/api/jobs/${jobId}/stop`, { method: "POST" });
  }

  return (
    <div className="space-y-6">
      <header>
        <p className="text-sm uppercase tracking-[0.16em] text-[var(--gold)]">Drafts</p>
        <h1 className="mt-2 font-[family-name:var(--font-serif)] text-5xl font-medium tracking-tight">
          {productName ? `${productName} replies` : "Drafted replies"}
        </h1>
      </header>
      <section className="panel space-y-4 p-5">
        <p className="text-sm text-[var(--muted)]">
          {drafts.length === 0
            ? "No reply is waiting. Draft replies from Coming, then they show up here."
            : `${drafts.length} waiting. Post replies sends each one directly and waits 2 minutes before the next. It does not like the post.`}
        </p>
        <div className="flex flex-wrap gap-2">
          <button className="btn" type="button" onClick={() => void postDrafts()} disabled={drafts.length === 0 || running}>
            {running ? "Posting replies…" : "Post replies"}
          </button>
          <button className="btn-danger" type="button" onClick={() => void stop()} disabled={!running}>
            Stop
          </button>
        </div>
        {error ? <p className="text-[var(--bad)]">{error}</p> : null}
        {job ? <JobLog message={job.message} progress={job.progress} logs={job.logs} error={job.error} /> : null}
      </section>
      <div className="space-y-3">
        {drafts.map((post) => (
          <article key={post.id} className="panel space-y-3 p-5">
            <a className="break-all text-[var(--gold)]" href={post.url} target="_blank" rel="noreferrer">
              {post.url}
            </a>
            <p className="whitespace-pre-wrap text-lg leading-8">{post.reply}</p>
            {post.post_id ? (
              <Link className="btn-ghost" href={`/studio/${post.post_id}`}>
                Review and publish
              </Link>
            ) : null}
          </article>
        ))}
      </div>
    </div>
  );
}
