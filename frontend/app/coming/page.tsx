"use client";

import { useEffect, useMemo, useState } from "react";

import { JobLog } from "@/components/post-card";
import { request } from "@/lib/api";
import type { ComingPost, Job, SessionInfo } from "@/lib/types";
import { useJob } from "@/lib/use-job";

type Sessions = { x: SessionInfo; chatgpt: SessionInfo };

export default function ComingPage() {
  const [posts, setPosts] = useState<ComingPost[]>([]);
  const [productName, setProductName] = useState("");
  const [country, setCountry] = useState("All");
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const waiting = posts.filter((post) => (post.status || "saved") === "saved").length;
  const ready = posts.filter((post) => (post.status === "drafted" || post.status === "posting") && (post.reply || "").trim()).length;
  const running = job?.status === "running";
  const countryCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const post of posts) {
      const name = post.country?.trim() || "Unknown";
      counts.set(name, (counts.get(name) || 0) + 1);
    }
    return [...counts.entries()].sort((left, right) => {
      if (left[0] === "Unknown") return 1;
      if (right[0] === "Unknown") return -1;
      return right[1] - left[1] || left[0].localeCompare(right[0]);
    });
  }, [posts]);
  const selectedCountry = countryCounts.some(([name]) => name === country) ? country : "All";
  const shown = selectedCountry === "All" ? posts : posts.filter((post) => (post.country?.trim() || "Unknown") === selectedCountry);

  async function load() {
    const [body, sessions] = await Promise.all([
      request<{ posts: ComingPost[]; product_name?: string }>("/api/finder/coming"),
      request<Sessions>("/api/sessions/status"),
    ]);
    setPosts(body.posts || []);
    setProductName(body.product_name || "");
    setSession(sessions.x || null);
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

  async function start() {
    setError(null);
    try {
      const started = await request<Job>("/api/coming/comment", { method: "POST" });
      setJobId(started.id);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not start comments.");
    }
  }

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

  async function checkLogin() {
    setError(null);
    try {
      const started = await request<Job>("/api/sessions/x/check", { method: "POST" });
      setJobId(started.id);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not open the X login.");
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <p className="text-sm uppercase tracking-[0.16em] text-[var(--gold)]">Coming</p>
        <h1 className="mt-2 font-[family-name:var(--font-serif)] text-5xl font-medium tracking-tight">{productName ? `${productName} posts` : "Saved posts"}</h1>
      </header>
      <section className="panel space-y-4 p-5">
        <p className="text-sm text-[var(--muted)]">
          {productName ? `${productName} has its own list. ` : ""}
          {posts.length} saved. {waiting} still need a draft. {ready} drafted. Draft replies writes them, waits 30 seconds between posts, and does not post. Post replies sends every drafted reply from this list, without a separate approval, waits 2 minutes between replies, and does not like the post. A post needs more than 500 views, or more than 500 likes, replies, and reposts together.
        </p>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-[var(--muted)]">
            X {session?.handle ? `@${session.handle.replace(/^@/, "")}` : session?.detail || "Not checked"}
          </p>
          <button className="btn-ghost" type="button" onClick={() => void checkLogin()} disabled={running}>
            {running && job?.type === "x_check" ? "Opening X…" : "X login"}
          </button>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn" type="button" onClick={() => void start()} disabled={waiting === 0 || running}>
            {running && job?.type === "coming_comments" ? "Drafting replies…" : "Draft replies"}
          </button>
          <button className="btn" type="button" onClick={() => void postDrafts()} disabled={ready === 0 || running}>
            {running && job?.type === "coming_post" ? "Posting replies…" : "Post replies"}
          </button>
          <button className="btn-danger" type="button" onClick={() => void stop()} disabled={!running}>
            Stop
          </button>
        </div>
        {error ? <p className="text-[var(--bad)]">{error}</p> : null}
        {job ? <JobLog message={job.message} progress={job.progress} logs={job.logs} error={job.error} /> : null}
      </section>
      {posts.length === 0 ? <p className="text-[var(--muted)]">{productName ? `None yet for ${productName}. Find posts first.` : "None yet. Find posts first."}</p> : null}
      {posts.length > 0 ? (
        <section className="panel space-y-3 p-5">
          <label className="block max-w-xs">
            <span className="label">Country</span>
            <select className="select" value={selectedCountry} onChange={(event) => setCountry(event.target.value)}>
              <option value="All">All countries</option>
              {countryCounts.map(([name, count]) => (
                <option key={name} value={name}>{name} ({count})</option>
              ))}
            </select>
          </label>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[var(--muted)]">
                <th className="pb-2 font-normal">Country</th>
                <th className="pb-2 font-normal">Posts</th>
              </tr>
            </thead>
            <tbody>
              {countryCounts.map(([name, count]) => (
                <tr key={name} className="border-t border-[var(--line)]">
                  <td className="py-2">
                    <button className="text-[var(--gold)]" type="button" onClick={() => setCountry(name)}>{name}</button>
                  </td>
                  <td className="py-2">{count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
      <div className="space-y-3">
        {shown.map((post) => (
          <article key={post.id} className="panel space-y-3 p-5">
            <a className="break-all text-[var(--gold)]" href={post.url} target="_blank" rel="noreferrer">{post.url}</a>
            <p className="text-sm text-[var(--muted)]">{post.country?.trim() || "Unknown"} · {post.status || "saved"}{post.views ? ` · ${post.views.toLocaleString()} views` : ""}</p>
            {post.note ? <p className="text-sm leading-6">{post.note}</p> : null}
            {post.reply ? <p className="whitespace-pre-wrap leading-7">{post.reply}</p> : null}
          </article>
        ))}
      </div>
    </div>
  );
}
