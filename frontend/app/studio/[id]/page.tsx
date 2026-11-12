"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { JobLog } from "@/components/post-card";
import { metric, request, weightedLength, when } from "@/lib/api";
import type { Analysis, Comment, Job, Post, Settings } from "@/lib/types";
import { useJob } from "@/lib/use-job";

type Detail = { post: Post; analysis: Analysis | null; comments: Comment[] };

export default function StudioPage() {
  const params = useParams<{ id: string }>();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<Comment | null>(null);
  const [allowPromotion, setAllowPromotion] = useState(false);
  const job = useJob(jobId);

  async function load() {
    const [body, pref] = await Promise.all([
      request<Detail>(`/api/posts/${params.id}`),
      request<{ settings: Settings }>("/api/settings"),
    ]);
    setDetail(body);
    setSettings(pref.settings);
    setAllowPromotion(pref.settings.allow_promotion);
    const next: Record<string, string> = {};
    for (const comment of body.comments) next[comment.id] = comment.text;
    setDrafts(next);
  }

  useEffect(() => {
    void load().catch((exc: Error) => setError(exc.message));
  }, [params.id]);

  useEffect(() => {
    if (job?.status === "done") {
      setNotice(job.result?.reason || job.message);
      void load();
    }
    if (job?.status === "error") setError(job.error || job.message);
  }, [job?.status]);

  async function run(action: () => Promise<void>) {
    setError(null);
    try {
      await action();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Request failed");
    }
  }

  async function generate() {
    await run(async () => {
      setNotice(null);
      const started = await request<Job>(`/api/posts/${params.id}/comments`, {
        method: "POST",
        body: JSON.stringify({ allow_promotion: allowPromotion }),
      });
      setJobId(started.id);
    });
  }

  async function save(comment: Comment) {
    await run(async () => {
      const updated = await request<{ comment: Comment }>(`/api/comments/${comment.id}`, {
        method: "PATCH",
        body: JSON.stringify({ text: drafts[comment.id] ?? comment.text }),
      });
      setDrafts((current) => ({ ...current, [updated.comment.id]: updated.comment.text }));
      await load();
    });
  }

  async function regenerate(comment: Comment) {
    await run(async () => {
      const started = await request<Job>(`/api/comments/${comment.id}/regenerate`, { method: "POST" });
      setJobId(started.id);
    });
  }

  async function approve(comment: Comment) {
    await run(async () => {
      await request(`/api/comments/${comment.id}/approve`, {
        method: "POST",
        body: JSON.stringify({ text: drafts[comment.id] ?? comment.text }),
      });
      await load();
    });
  }

  async function publish(comment: Comment) {
    await run(async () => {
      const started = await request<Job | { already?: boolean; event?: { detail?: string } }>(`/api/comments/${comment.id}/publish`, {
        method: "POST",
        body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
      });
      setConfirming(null);
      if ("already" in started && started.already) {
        setNotice(started.event?.detail || "Already published.");
        await load();
        return;
      }
      setJobId((started as Job).id);
    });
  }

  async function confirm(comment: Comment) {
    await run(async () => {
      const started = await request<Job>(`/api/comments/${comment.id}/confirm`, { method: "POST" });
      setJobId(started.id);
    });
  }

  async function copy(text: string) {
    await navigator.clipboard.writeText(text);
    setNotice("Copied.");
  }

  const post = detail?.post;

  return (
    <div className="space-y-6">
      <Link href={post ? `/posts/${post.id}` : "/finder"} className="text-sm text-[var(--gold)]">Back to the post</Link>
      <header>
        <p className="text-sm uppercase tracking-[0.16em] text-[var(--gold)]">Comment studio</p>
        <h1 className="mt-2 font-[family-name:var(--font-serif)] text-5xl font-medium tracking-tight">Three replies. You choose one.</h1>
      </header>
      {error ? <p className="text-[var(--bad)]">{error}</p> : null}
      {notice ? <p className="text-[var(--muted)]">{notice}</p> : null}
      {job ? <JobLog message={job.message} progress={job.progress} logs={job.logs} error={job.error} /> : null}
      <div className="grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
        <section className="panel p-5">
          <div className="text-sm text-[var(--muted)]">{post ? `@${post.handle} · ${when(post.created_at)}` : "Loading"}</div>
          <p className="mt-3 whitespace-pre-wrap leading-7">{post?.text}</p>
          <div className="mt-4 flex gap-3 text-sm text-[var(--muted)]">
            <span>{metric(post?.latest_views)} views</span>
            <span>{metric(post?.latest_likes)} likes</span>
            <span>{metric(post?.latest_replies)} replies</span>
          </div>
          {detail?.analysis?.payload?.reply_angle ? (
            <p className="mt-4 text-sm text-[var(--muted)]">Angle: {detail.analysis.payload.reply_angle}</p>
          ) : null}
          <label className="mt-5 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={allowPromotion} onChange={(event) => setAllowPromotion(event.target.checked)} />
            Allow a {settings?.product_name || "product"} mention when it fits
          </label>
          <button className="btn mt-4" type="button" onClick={generate} disabled={job?.status === "running"}>Generate three replies</button>
        </section>
        <section className="space-y-3">
          {(detail?.comments || []).length === 0 ? <p className="text-[var(--muted)]">No drafts yet.</p> : null}
          {(detail?.comments || []).map((comment) => {
            const text = drafts[comment.id] ?? comment.text;
            return (
              <article key={comment.id} className="panel p-4">
                <div className="mb-2 flex items-center justify-between text-sm text-[var(--muted)]">
                  <span className="uppercase tracking-[0.12em]">{comment.variant} · v{comment.version}</span>
                  <span>{comment.status} · {weightedLength(text)} / 280</span>
                </div>
                <textarea className="area" value={text} onChange={(event) => setDrafts((current) => ({ ...current, [comment.id]: event.target.value }))} />
                <div className="mt-3 flex flex-wrap gap-2">
                  <button className="btn-ghost" type="button" onClick={() => save(comment)}>Save edit</button>
                  <button className="btn-ghost" type="button" onClick={() => regenerate(comment)}>Regenerate</button>
                  <button className="btn-ghost" type="button" onClick={() => copy(text)}>Copy</button>
                  <button className="btn" type="button" onClick={() => approve(comment)}>Approve</button>
                  <button className="btn" type="button" onClick={() => setConfirming(comment)} disabled={comment.status !== "approved" && comment.status !== "uncertain"}>Publish</button>
                  {comment.status === "uncertain" ? <button className="btn-ghost" type="button" onClick={() => confirm(comment)}>Check again</button> : null}
                </div>
              </article>
            );
          })}
        </section>
      </div>
      {confirming ? (
        <div className="fixed inset-0 z-20 grid place-items-center bg-black/60 p-4">
          <div className="panel max-w-lg p-5">
            <h2 className="font-[family-name:var(--font-serif)] text-3xl">Publish this reply?</h2>
            <p className="mt-3 text-sm text-[var(--muted)]">This sends one reply from the signed-in X account. A retry with the same action will not send it twice.</p>
            <p className="mt-4 whitespace-pre-wrap">{drafts[confirming.id] ?? confirming.text}</p>
            <div className="mt-5 flex gap-2">
              <button className="btn" type="button" onClick={() => publish(confirming)}>Publish reply</button>
              <button className="btn-ghost" type="button" onClick={() => setConfirming(null)}>Cancel</button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
