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
          <ScanSummary posts={posts} comments={comments} todayKey={todayKey} />
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

type DayRow = {
  day: string;
  posts: number;
  postViews: number;
  comments: number;
  commentViews: number;
};

function shiftDay(key: string, delta: number): string {
  const [year, month, day] = key.split("-").map(Number);
  const stamp = new Date(year, month - 1, day);
  stamp.setDate(stamp.getDate() + delta);
  return localDay(stamp);
}

function labelDay(key: string): string {
  const [year, month, day] = key.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function hasLink(text: string): boolean {
  return /https?:\/\//i.test(text);
}

function saysI(text: string): boolean {
  return /\bi(?:'m| am)\b/i.test(text);
}

function hasHash(text: string): boolean {
  return /(^|\s)#\w+/.test(text);
}

function average(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function countWhere(items: ActivityCard[], test: (text: string) => boolean): number {
  return items.filter((item) => test(item.text || "")).length;
}

function opening(item: ActivityCard | undefined): string {
  const text = (item?.text || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length > 90 ? `${text.slice(0, 90).trim()}…` : text;
}

function dayRows(posts: ActivityCard[], comments: ActivityCard[]): DayRow[] {
  const days = new Map<string, DayRow>();
  function row(day: string): DayRow {
    const current = days.get(day);
    if (current) return current;
    const created = { day, posts: 0, postViews: 0, comments: 0, commentViews: 0 };
    days.set(day, created);
    return created;
  }
  for (const item of posts) {
    const day = localDay(item.created_at);
    if (!day) continue;
    const current = row(day);
    current.posts += 1;
    current.postViews += item.views ?? 0;
  }
  for (const item of comments) {
    const day = localDay(item.created_at);
    if (!day) continue;
    const current = row(day);
    current.comments += 1;
    current.commentViews += item.views ?? 0;
  }
  return [...days.values()].sort((left, right) => left.day.localeCompare(right.day));
}

function viewReason(days: DayRow[], todayKey: string): string {
  if (days.length === 0) return "This scan has no dated posts or comments to compare.";
  const listed = days.map((row) => `${labelDay(row.day)} ${ (row.postViews + row.commentViews).toLocaleString() }`).join(", ");
  const today = days.find((row) => row.day === todayKey);
  const earlier = [...days].reverse().find((row) => row.day < todayKey);
  if (!today) return `Views by day: ${listed}. Nothing in this scan is from today.`;
  const todayTotal = today.postViews + today.commentViews;
  if (!earlier) {
    return `Views by day: ${listed}. Today is the only day in this scan, with ${todayTotal.toLocaleString()} views.`;
  }
  const earlierTotal = earlier.postViews + earlier.commentViews;
  const gap = todayTotal - earlierTotal;
  const direction = gap > 0 ? `${gap.toLocaleString()} above` : gap < 0 ? `${Math.abs(gap).toLocaleString()} below` : "level with";
  const todayEach = today.comments ? Math.round(today.commentViews / today.comments) : 0;
  const earlierEach = earlier.comments ? Math.round(earlier.commentViews / earlier.comments) : 0;
  return `Views by day, posts and comments together: ${listed}. Today has ${todayTotal.toLocaleString()} views, which is ${direction} ${labelDay(earlier.day)}'s ${earlierTotal.toLocaleString()}. Today's post has ${today.postViews.toLocaleString()} views, against ${earlier.postViews.toLocaleString()} on ${labelDay(earlier.day)}. Comments carry the change: ${today.comments} comments have ${today.commentViews.toLocaleString()} views today, about ${todayEach} each. ${labelDay(earlier.day)} had ${earlier.comments} comments and ${earlier.commentViews.toLocaleString()} views, about ${earlierEach} each.`;
}

function likeReason(posts: ActivityCard[], comments: ActivityCard[]): string {
  if (posts.length === 0) return "This scan has no posts to check for likes.";
  const liked = posts.filter((item) => (item.likes ?? 0) > 0);
  if (liked.length > 0) {
    const best = topByViews(liked)[0];
    return `${liked.length} of ${posts.length} posts have at least one like. The most-viewed of those has ${(best.views ?? 0).toLocaleString()} views and ${(best.likes ?? 0).toLocaleString()} likes.`;
  }
  const views = posts.map((item) => item.views ?? 0).sort((left, right) => right - left);
  const linked = countWhere(posts, hasLink);
  const tagged = countWhere(posts, hasHash);
  const commentLikes = comments.filter((item) => (item.likes ?? 0) > 0).length;
  return `All ${posts.length} posts show 0 likes. Their view counts are ${views.map((value) => value.toLocaleString()).join(", ")}, so each post has been seen by a small number of people. ${linked} include a link and ${tagged} include a hashtag, and they name the product, so they read as announcements. The scan also stored 0 likes on ${comments.length - commentLikes} of ${comments.length} comments, so a later scan is needed before a like on a post can be compared with a like on a comment.`;
}

function commentReason(comments: ActivityCard[], todayKey: string): string {
  const yesterdayKey = shiftDay(todayKey, -1);
  const today = comments.filter((item) => localDay(item.created_at) === todayKey);
  const yesterday = comments.filter((item) => localDay(item.created_at) === yesterdayKey);
  if (today.length === 0 && yesterday.length === 0) return "This scan has no comments from today or yesterday.";
  if (yesterday.length === 0) return `Today has ${today.length} comments. Yesterday has none in this scan, so there is no comment style to compare.`;
  if (today.length === 0) return `Yesterday has ${yesterday.length} comments. Today has none in this scan.`;
  const todayTop = topByViews(today)[0];
  const yesterdayTop = topByViews(yesterday)[0];
  return `Yesterday's ${yesterday.length} comments average ${Math.round(average(yesterday.map((item) => (item.text || "").length)))} characters and ${Math.round(average(yesterday.map((item) => item.views ?? 0)))} views. ${countWhere(yesterday, hasLink)} include a link and ${countWhere(yesterday, saysI)} say "I'm" or "I am". Today's ${today.length} comments average ${Math.round(average(today.map((item) => (item.text || "").length)))} characters and ${Math.round(average(today.map((item) => item.views ?? 0)))} views. ${countWhere(today, hasLink)} include a link and ${countWhere(today, saysI)} say "I'm" or "I am". The highest comment today starts "${opening(todayTop)}". The highest yesterday starts "${opening(yesterdayTop)}".`;
}

function ScanSummary({ posts, comments, todayKey }: { posts: ActivityCard[]; comments: ActivityCard[]; todayKey: string }) {
  const days = dayRows(posts, comments);
  const lines = [viewReason(days, todayKey), likeReason(posts, comments), commentReason(comments, todayKey)];
  return (
    <section className="panel space-y-4 p-5">
      <h2 className="font-[family-name:var(--font-serif)] text-2xl">Why the numbers look like this</h2>
      <div className="overflow-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-[0.12em] text-[var(--muted)]">
            <tr>
              <th className="py-2 pr-3 font-normal">Day</th>
              <th className="py-2 pr-3 font-normal">Posts</th>
              <th className="py-2 pr-3 font-normal">Post views</th>
              <th className="py-2 pr-3 font-normal">Comments</th>
              <th className="py-2 pr-3 font-normal">Comment views</th>
              <th className="py-2 font-normal">Total</th>
            </tr>
          </thead>
          <tbody>
            {days.map((row) => (
              <tr key={row.day} className={row.day === todayKey ? "text-[var(--gold)]" : ""}>
                <td className="py-2 pr-3">{labelDay(row.day)}{row.day === todayKey ? " · today" : ""}</td>
                <td className="py-2 pr-3">{row.posts}</td>
                <td className="py-2 pr-3">{row.postViews.toLocaleString()}</td>
                <td className="py-2 pr-3">{row.comments}</td>
                <td className="py-2 pr-3">{row.commentViews.toLocaleString()}</td>
                <td className="py-2">{(row.postViews + row.commentViews).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {lines.map((line) => (
        <p key={line.slice(0, 40)} className="text-sm leading-6">{line}</p>
      ))}
    </section>
  );
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
