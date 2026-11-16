"use client";

import { useEffect, useState } from "react";

import { request, when } from "@/lib/api";
import type { ActivityCard, ActivitySnapshot, Job } from "@/lib/types";
import { useJob } from "@/lib/use-job";

export default function RanksPage() {
  const [activity, setActivity] = useState<ActivitySnapshot | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);

  async function load() {
    const snapshot = await request<ActivitySnapshot>("/api/activity");
    setActivity(snapshot);
  }

  useEffect(() => {
    void load()
      .catch((exc: Error) => setError(exc.message))
      .finally(() => setReady(true));
    function onProduct() {
      void load().catch(() => undefined);
    }
    window.addEventListener("xbot-product", onProduct);
    return () => window.removeEventListener("xbot-product", onProduct);
  }, []);

  useEffect(() => {
    if (job?.status === "done" || job?.status === "stopped") {
      void load().catch((exc: Error) => setError(exc.message));
      setJobId(null);
    }
    if (job?.status === "error") setError(job.error || job.message);
  }, [job]);

  async function refresh() {
    setError(null);
    const started = await request<Job>("/api/activity/scan", { method: "POST" });
    setJobId(started.id);
  }

  const posts = activity?.posts ?? [];
  const comments = activity?.comments ?? [];
  const todayKey = localDay(new Date());
  const todayComments = comments.filter((item) => localDay(item.created_at) === todayKey);
  const todayPosts = posts.filter((item) => localDay(item.created_at) === todayKey);
  const overallPosts = topByViews(posts);
  const todayRankedPosts = topByViews(todayPosts);
  const overall = topByViews(comments);
  const today = topByViews(todayComments);
  const hasScan = Boolean(activity?.collected_at);
  const busy = job?.status === "running" || Boolean(jobId && !job);
  const postViews = sumViews(posts);
  const commentViews = sumViews(comments);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm uppercase tracking-[0.16em] text-[var(--gold)]">Ranks</p>
          <h1 className="mt-2 font-[family-name:var(--font-serif)] text-5xl font-medium tracking-tight">Highest viewed posts and comments.</h1>
          <p className="mt-3 max-w-xl text-sm text-[var(--muted)]">
            Every post and comment from the last scan of your account. Each list is the five with the most views, overall and from today.
          </p>
        </div>
        <button className="btn" type="button" disabled={busy} onClick={() => void refresh().catch((exc: Error) => setError(exc.message))}>
          {busy ? "Reading your account…" : "Refresh scan"}
        </button>
      </header>

      {error ? <p className="text-[var(--bad)]">{error}</p> : null}
      {busy ? <p className="text-sm text-[var(--muted)]">{job?.message || "Reading posts and comments."}</p> : null}
      {!ready && !hasScan ? <p className="text-sm text-[var(--muted)]">Loading the last scan.</p> : null}

      {ready && !hasScan && !busy ? (
        <section className="panel p-8 text-center">
          <p className="font-[family-name:var(--font-serif)] text-3xl">No scan yet.</p>
          <p className="mx-auto mt-3 max-w-md text-sm text-[var(--muted)]">
            Refresh the scan to read the posts and comments on this account. Ranks use those view counts.
          </p>
        </section>
      ) : null}

      {hasScan ? (
        <>
          <p className="text-sm text-[var(--muted)]">
            {activity?.handle ? `@${activity.handle}` : "Your account"}
            {activity?.collected_at ? ` · scanned ${when(activity.collected_at)}` : ""}
          </p>
          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Posts" value={posts.length} detail={`${todayPosts.length} from today · ${postViews.toLocaleString()} views`} />
            <Stat label="Comments" value={comments.length} detail={`${todayComments.length} from today · ${commentViews.toLocaleString()} views`} />
            <Stat label="Top post" value={overallPosts[0]?.views ?? 0} detail="highest views overall" />
            <Stat label="Top comment" value={overall[0]?.views ?? 0} detail="highest views overall" />
          </section>
          <p className="text-sm leading-6 text-[var(--muted)]">
            {posts.length} posts hold {postViews.toLocaleString()} views. {comments.length} comments hold {commentViews.toLocaleString()} views.
            {overallPosts[0] ? ` The strongest post has ${overallPosts[0].views?.toLocaleString() ?? 0} views.` : " No posts were in this scan."}
            {overall[0] ? ` The strongest comment has ${overall[0].views?.toLocaleString() ?? 0} views.` : " No comments were in this scan."}
          </p>
          <div className="grid gap-4 lg:grid-cols-2">
            <RankList title="Posts overall" empty="No posts in this scan." items={overallPosts} />
            <RankList title="Posts today" empty="No posts from today are in this scan." items={todayRankedPosts} />
            <RankList title="Comments overall" empty="No comments in this scan." items={overall} />
            <RankList title="Comments today" empty="No comments from today are in this scan." items={today} />
          </div>
        </>
      ) : null}
    </div>
  );
}

function localDay(value?: string | Date | null): string {
  if (!value) return "";
  const stamp = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(stamp.getTime())) return "";
  const month = String(stamp.getMonth() + 1).padStart(2, "0");
  const day = String(stamp.getDate()).padStart(2, "0");
  return `${stamp.getFullYear()}-${month}-${day}`;
}

function sumViews(items: ActivityCard[]): number {
  return items.reduce((sum, item) => sum + (item.views ?? 0), 0);
}

function topByViews(items: ActivityCard[]): ActivityCard[] {
  return [...items].sort((left, right) => (right.views ?? 0) - (left.views ?? 0) || (right.likes ?? 0) - (left.likes ?? 0)).slice(0, 5);
}

function Stat({ label, value, detail }: { label: string; value: number; detail: string }) {
  return (
    <article className="panel p-4">
      <p className="text-xs uppercase tracking-[0.16em] text-[var(--muted)]">{label}</p>
      <p className="mt-2 font-[family-name:var(--font-serif)] text-4xl">{value.toLocaleString()}</p>
      <p className="text-sm text-[var(--muted)]">{detail}</p>
    </article>
  );
}

function RankList({ title, empty, items }: { title: string; empty: string; items: ActivityCard[] }) {
  return (
    <section className="panel space-y-3 p-5">
      <h2 className="font-[family-name:var(--font-serif)] text-2xl">{title}</h2>
      {items.length === 0 ? <p className="text-sm text-[var(--muted)]">{empty}</p> : null}
      {items.map((item, index) => (
        <article key={item.id} className="rounded-2xl border border-[var(--line)] p-4">
          <div className="flex items-baseline justify-between gap-3">
            <p className="text-xs uppercase tracking-[0.16em] text-[var(--gold)]">#{index + 1}</p>
            <p className="text-sm text-[var(--muted)]">{(item.views ?? 0).toLocaleString()} views</p>
          </div>
          {item.reply_to ? <p className="mt-2 text-xs uppercase text-[var(--muted)]">Replying to @{item.reply_to}</p> : null}
          <p className="mt-2 leading-relaxed">{item.text}</p>
          <p className="mt-3 text-sm text-[var(--muted)]">
            {(item.likes ?? 0).toLocaleString()} likes · {(item.replies ?? 0).toLocaleString()} replies
            {item.created_at ? ` · ${when(item.created_at)}` : ""}
          </p>
          <a className="mt-3 inline-flex text-sm text-[var(--gold)]" href={item.url} target="_blank" rel="noreferrer">
            Open on X
          </a>
        </article>
      ))}
    </section>
  );
}
