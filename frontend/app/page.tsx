"use client";

import Link from "next/link";
import { FormEvent, MouseEvent, useEffect, useState } from "react";

import { JobLog } from "@/components/post-card";
import { PostCard } from "@/components/post-card";
import { metric, request } from "@/lib/api";
import type { Health, Job, Overview, Post, SessionInfo } from "@/lib/types";
import { useJob } from "@/lib/use-job";

type Sessions = { x: SessionInfo; chatgpt: SessionInfo; database: string; database_detail: string };

export default function DashboardPage() {
  const [health, setHealth] = useState<Health | null>(null);
  const [sessions, setSessions] = useState<Sessions | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [posts, setPosts] = useState<Post[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [topic, setTopic] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<string | null>(null);
  const live = useJob(activeJob);

  async function load() {
    const [healthBody, sessionBody, overviewBody, postBody, jobBody] = await Promise.all([
      request<Health>("/api/health"),
      request<Sessions>("/api/sessions/status"),
      request<Overview>("/api/analytics/overview"),
      request<{ posts: Post[] }>("/api/finder/posts?limit=4"),
      request<{ jobs: Job[] }>("/api/jobs?limit=6"),
    ]);
    setHealth(healthBody);
    setSessions(sessionBody);
    setOverview(overviewBody);
    setPosts(postBody.posts);
    setJobs(jobBody.jobs);
  }

  useEffect(() => {
    void load().catch((exc: Error) => setError(exc.message));
  }, []);

  useEffect(() => {
    if (live?.status === "done" || live?.status === "error") void load();
  }, [live?.status]);

  async function check(path: string) {
    setError(null);
    try {
      const job = await request<Job>(path, { method: "POST" });
      setActiveJob(job.id);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not start the check");
    }
  }

  function search(event: FormEvent) {
    event.preventDefault();
    const query = new URLSearchParams({ topic });
    window.location.href = `/finder?${query.toString()}`;
  }

  const counts = overview?.counts;

  return (
    <div className="space-y-8">
      <header>
        <p className="text-sm uppercase tracking-[0.16em] text-[var(--gold)]">Desk</p>
        <h1 className="mt-2 font-[family-name:var(--font-serif)] text-5xl font-medium tracking-tight">What XBot has in hand.</h1>
      </header>

      {error ? <p className="text-[var(--bad)]">{error}</p> : null}
      {health && health.database !== "supabase" ? (
        <p className="panel p-4 text-sm text-[var(--muted)]">{health.database_detail} The app keeps working from the local store until the Supabase tables exist.</p>
      ) : null}

      <section className="grid gap-3 md:grid-cols-4">
        <Stat label="Discovered" value={counts?.posts ?? 0} />
        <Stat label="Analysed" value={counts?.analyses ?? 0} />
        <Stat label="Drafts" value={counts?.comments ?? 0} />
        <Stat label="Published" value={counts?.published ?? 0} />
      </section>

      <section className="grid gap-4 lg:grid-cols-[1.2fr_.8fr]">
        <div className="panel p-5">
          <h2 className="text-lg">Sessions</h2>
          <div className="mt-4 space-y-3">
            <SessionRow info={sessions?.x} fallback="X" />
            <SessionRow info={sessions?.chatgpt} fallback="ChatGPT" />
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <button className="btn" type="button" onClick={() => check("/api/sessions/x/check")}>Check X</button>
            <button className="btn-ghost" type="button" onClick={() => check("/api/sessions/chatgpt/check")}>Check ChatGPT</button>
          </div>
          {live ? <div className="mt-4"><JobLog message={live.message} progress={live.progress} logs={live.logs} error={live.error} /></div> : null}
          <RemoteBrowser active={live?.status === "running"} />
        </div>
        <form className="panel p-5" onSubmit={search}>
          <h2 className="text-lg">Quick search</h2>
          <label className="label mt-4" htmlFor="topic">Topic</label>
          <input id="topic" className="field" value={topic} onChange={(event) => setTopic(event.target.value)} placeholder="AI agents" />
          <button className="btn mt-4" type="submit">Open Finder</button>
          <p className="mt-4 text-sm text-[var(--muted)]">Database: {sessions?.database || health?.database || "…"} · {metric(counts?.uncertain)} unconfirmed replies</p>
        </form>
      </section>

      <section className="grid gap-4 lg:grid-cols-[1.1fr_.9fr]">
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-lg">Recent posts</h2>
            <Link href="/finder" className="text-sm text-[var(--gold)]">Finder</Link>
          </div>
          {posts.length === 0 ? <p className="text-[var(--muted)]">No posts saved yet.</p> : posts.map((post) => <PostCard key={post.id} post={post} />)}
        </div>
        <div className="panel p-5">
          <h2 className="text-lg">Recent jobs</h2>
          <ul className="mt-4 space-y-3 text-sm">
            {jobs.length === 0 ? <li className="text-[var(--muted)]">No jobs yet.</li> : null}
            {jobs.map((job) => (
              <li key={job.id} className="flex items-center justify-between gap-3 border-b border-[var(--line)] pb-3">
                <span>{job.type.replaceAll("_", " ")}</span>
                <span className="text-[var(--muted)]">{job.status} · {job.progress}%</span>
              </li>
            ))}
          </ul>
        </div>
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <article className="panel stat">
      <span className="text-sm uppercase tracking-[0.14em] text-[var(--muted)]">{label}</span>
      <b>{value}</b>
    </article>
  );
}

function RemoteBrowser({ active }: { active: boolean }) {
  const [tick, setTick] = useState(0);
  const [text, setText] = useState("");

  useEffect(() => {
    if (!active) return undefined;
    setTick(Date.now());
    const timer = window.setInterval(() => setTick(Date.now()), 1500);
    return () => window.clearInterval(timer);
  }, [active]);

  if (!active) return null;

  async function click(event: MouseEvent<HTMLImageElement>) {
    const img = event.currentTarget;
    const rect = img.getBoundingClientRect();
    if (!rect.width || !rect.height || !img.naturalWidth) return;
    const x = Math.round((event.clientX - rect.left) * (img.naturalWidth / rect.width));
    const y = Math.round((event.clientY - rect.top) * (img.naturalHeight / rect.height));
    await request("/api/sessions/input", { method: "POST", body: JSON.stringify({ x, y }) });
  }

  async function sendText(event: FormEvent) {
    event.preventDefault();
    if (!text) return;
    await request("/api/sessions/input", { method: "POST", body: JSON.stringify({ text }) });
    setText("");
  }

  return (
    <div className="mt-4 space-y-2">
      <p className="text-sm text-[var(--muted)]">Server browser. Click the picture or type a code. A Chrome window also opens when this app is running on your computer.</p>
      <img
        src={`/api/sessions/screen?t=${tick}`}
        alt="Server browser"
        className="w-full rounded-lg border border-[var(--line)] bg-black"
        onClick={(event) => void click(event)}
      />
      <form className="flex gap-2" onSubmit={(event) => void sendText(event)}>
        <input className="field" value={text} onChange={(event) => setText(event.target.value)} placeholder="Code or text" />
        <button className="btn" type="submit">Type</button>
        <button className="btn-ghost" type="button" onClick={() => void request("/api/sessions/input", { method: "POST", body: JSON.stringify({ key: "Enter" }) })}>Enter</button>
      </form>
    </div>
  );
}

function SessionRow({ info, fallback }: { info?: SessionInfo; fallback: string }) {
  const status = info?.status || "not_checked";
  const tone = status === "signed_in" ? "ok" : status === "needs_manual" ? "warn" : "bad";
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="pill"><span className={`dot ${status === "not_checked" ? "" : tone}`} />{fallback}</span>
      <span className="text-sm text-[var(--muted)]">{info?.handle ? `@${info.handle}` : info?.detail || "Not checked"}</span>
    </div>
  );
}
