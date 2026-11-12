"use client";

import { useEffect, useState } from "react";

import { JobLog } from "@/components/post-card";
import { request } from "@/lib/api";
import type { ComingPost, Job } from "@/lib/types";
import { useJob } from "@/lib/use-job";

export default function ComingPage() {
  const [posts, setPosts] = useState<ComingPost[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const waiting = posts.filter((post) => {
    const status = post.status || "saved";
    return status === "saved" || status === "drafted";
  }).length;

  async function load() {
    const body = await request<{ posts: ComingPost[] }>("/api/finder/coming");
    setPosts(body.posts || []);
  }

  useEffect(() => {
    void load().catch((exc: Error) => setError(exc.message));
  }, []);

  useEffect(() => {
    if (!job) return;
    if (job.status === "running") {
      const timer = window.setInterval(() => {
        void request<{ posts: ComingPost[] }>("/api/finder/coming")
          .then((body) => setPosts(body.posts || []))
          .catch(() => undefined);
      }, 2000);
      return () => window.clearInterval(timer);
    }
    void load().catch((exc: Error) => setError(exc.message));
  }, [job]);

  async function start() {
    setError(null);
    try {
      const started = await request<Job>("/api/coming/comment", { method: "POST" });
      setJobId(started.id);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not start comments.");
    }
  }

  async function stop() {
    if (!jobId) return;
    await request(`/api/jobs/${jobId}/stop`, { method: "POST" });
  }

  return (
    <div className="space-y-6">
      <header>
        <p className="text-sm uppercase tracking-[0.16em] text-[var(--gold)]">Coming</p>
        <h1 className="mt-2 font-[family-name:var(--font-serif)] text-5xl font-medium tracking-tight">Saved posts, then one reply each.</h1>
      </header>
      <section className="panel space-y-4 p-5">
        <p className="text-sm text-[var(--muted)]">
          {posts.length} saved. {waiting} left. Each reply is posted, that post is liked, then the next one waits 30 seconds.
        </p>
        <div className="flex flex-wrap gap-2">
          <button className="btn" type="button" onClick={() => void start()} disabled={waiting === 0 || job?.status === "running"}>
            {job?.status === "running" ? "Posting replies…" : "Start commenting"}
          </button>
          <button className="btn-danger" type="button" onClick={() => void stop()} disabled={job?.status !== "running"}>
            Stop
          </button>
        </div>
        {error ? <p className="text-[var(--bad)]">{error}</p> : null}
        {job ? <JobLog message={job.message} progress={job.progress} logs={job.logs} error={job.error} /> : null}
      </section>
      {posts.length === 0 ? <p className="text-[var(--muted)]">None yet. Find posts first.</p> : null}
      <div className="space-y-3">
        {posts.map((post) => (
          <article key={post.id} className="panel space-y-3 p-5">
            <a className="break-all text-[var(--gold)]" href={post.url} target="_blank" rel="noreferrer">{post.url}</a>
            <p className="text-sm text-[var(--muted)]">{post.status || "saved"}{post.views ? ` · ${post.views.toLocaleString()} views` : ""}</p>
            {post.note ? <p className="text-sm leading-6">{post.note}</p> : null}
            {post.reply ? <p className="whitespace-pre-wrap leading-7">{post.reply}</p> : null}
          </article>
        ))}
      </div>
    </div>
  );
}
