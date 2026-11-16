"use client";

import Link from "next/link";
import { FormEvent, MouseEvent, useEffect, useState } from "react";

import { JobLog } from "@/components/post-card";
import { PostCard } from "@/components/post-card";
import { metric, request, when } from "@/lib/api";
import type { ActivitySnapshot, Health, Job, Overview, Post, SessionInfo } from "@/lib/types";
import { useJob } from "@/lib/use-job";

type Sessions = { x: SessionInfo; chatgpt: SessionInfo; database: string; database_detail: string };

export default function DashboardPage() {
  const [health, setHealth] = useState<Health | null>(null);
  const [sessions, setSessions] = useState<Sessions | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [scan, setScan] = useState<ActivitySnapshot | undefined>(undefined);
  const [posts, setPosts] = useState<Post[] | null>(null);
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [topic, setTopic] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<string | null>(null);
  const live = useJob(activeJob);

  async function load() {
    const results = await Promise.allSettled([
      request<Health>("/api/health").then(setHealth),
      request<Sessions>("/api/sessions/status").then(setSessions),
      request<Overview>("/api/analytics/overview").then(setOverview),
      request<ActivitySnapshot>("/api/activity").then(setScan),
      request<{ posts: Post[] }>("/api/finder/posts?limit=4").then((body) => setPosts(body.posts || [])),
      request<{ jobs: Job[] }>("/api/jobs?limit=6").then((body) => setJobs(body.jobs || [])),
    ]);
    const failure = results.find((item) => item.status === "rejected");
    if (failure?.status === "rejected") {
      const reason = failure.reason;
      setError(reason instanceof Error ? reason.message : "Could not load the desk");
      return;
    }
    setError(null);
  }

  useEffect(() => {
    void load().catch((exc: Error) => setError(exc.message));
    function onProduct() {
      void load().catch((exc: Error) => setError(exc.message));
    }
    window.addEventListener("xbot-product", onProduct);
    window.addEventListener("focus", onProduct);
    return () => {
      window.removeEventListener("xbot-product", onProduct);
      window.removeEventListener("focus", onProduct);
    };
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
  const fresh = Boolean(scan?.collected_at);
  const posted = fresh ? scan?.posts_created ?? 0 : null;

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

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Stat label="Posts" value={scan === undefined ? null : posted} detail="you wrote" />
        <Stat label="Comments" value={scan === undefined ? null : fresh ? scan?.comments_dropped ?? 0 : null} detail="you left" />
        <Stat label="Views" value={scan === undefined ? null : fresh ? scan?.views ?? 0 : null} detail="posts and replies" />
        <Stat label="Likes" value={scan === undefined ? null : fresh ? scan?.likes ?? 0 : null} detail="posts and replies" />
        <Stat label="Replies" value={scan === undefined ? null : fresh ? scan?.replies_received ?? 0 : null} detail="people wrote back" />
      </section>
      <p className="text-sm text-[var(--muted)]">
        {fresh
          ? `Latest activity refresh${scan?.handle ? ` · @${scan.handle}` : ""} · ${when(scan?.collected_at)}`
          : "Refresh activity on Analysis. These numbers follow that scan."}
      </p>

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
            <h2 className="text-lg">{overview?.product_name ? `${overview.product_name} posts` : "Recent posts"}</h2>
            <Link href="/finder" className="text-sm text-[var(--gold)]">Finder</Link>
          </div>
          {posts === null ? (
            <p className="text-[var(--muted)]">Loading posts…</p>
          ) : posts.length === 0 ? (
            <p className="text-[var(--muted)]">{overview?.product_name ? `No posts saved for ${overview.product_name} yet.` : "No posts saved yet."}</p>
          ) : posts.map((post) => <PostCard key={post.id} post={post} />)}
        </div>
        <div className="panel p-5">
          <h2 className="text-lg">Recent jobs</h2>
          <ul className="mt-4 space-y-3 text-sm">
            {jobs === null ? <li className="text-[var(--muted)]">Loading jobs…</li> : null}
            {jobs?.length === 0 ? <li className="text-[var(--muted)]">No jobs yet.</li> : null}
            {(jobs ?? []).map((job) => (
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

function Stat({ label, value, detail }: { label: string; value: number | null; detail?: string }) {
  return (
    <article className="panel stat">
      <span className="text-sm uppercase tracking-[0.14em] text-[var(--muted)]">{label}</span>
      <b>{value === null ? "…" : value.toLocaleString()}</b>
      {detail ? <span className="mt-1 block text-sm text-[var(--muted)]">{detail}</span> : null}
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
