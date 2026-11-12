"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { JobLog } from "@/components/post-card";
import { metric, request, when } from "@/lib/api";
import type { Analysis, Comment, Job, Post } from "@/lib/types";
import { useJob } from "@/lib/use-job";

type Detail = { post: Post; metrics: { views: number | null; likes: number | null; replies: number | null; collected_at: string }[]; analysis: Analysis | null; comments: Comment[] };

export default function PostPage() {
  const params = useParams<{ id: string }>();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);

  async function load() {
    setDetail(await request<Detail>(`/api/posts/${params.id}`));
  }

  useEffect(() => {
    void load().catch((exc: Error) => setError(exc.message));
  }, [params.id]);

  useEffect(() => {
    if (job?.status === "done") void load();
  }, [job?.status]);

  async function analyse() {
    setError(null);
    try {
      const started = await request<Job>(`/api/posts/${params.id}/analyse`, { method: "POST" });
      setJobId(started.id);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Analysis failed");
    }
  }

  const post = detail?.post;
  const payload = detail?.analysis?.payload;

  return (
    <div className="space-y-6">
      <Link href="/finder" className="text-sm text-[var(--gold)]">Back to Finder</Link>
      {error ? <p className="text-[var(--bad)]">{error}</p> : null}
      {!post ? <p className="text-[var(--muted)]">Loading post…</p> : (
        <>
          <header>
            <p className="text-sm text-[var(--muted)]">@{post.handle} · {when(post.created_at)}</p>
            <h1 className="mt-2 font-[family-name:var(--font-serif)] text-4xl font-medium tracking-tight">{post.author || "Post"}</h1>
          </header>
          <article className="panel p-5">
            <p className="whitespace-pre-wrap text-lg leading-8">{post.text}</p>
            <div className="mt-4 flex flex-wrap gap-4 text-sm text-[var(--muted)]">
              <span>{metric(post.latest_views)} views</span>
              <span>{metric(post.latest_likes)} likes</span>
              <span>{metric(post.latest_replies)} replies</span>
              <span>{post.relevance ?? "—"} relevance</span>
            </div>
            <div className="mt-5 flex flex-wrap gap-2">
              <button className="btn" type="button" onClick={analyse} disabled={job?.status === "running"}>Analyse with ChatGPT</button>
              <Link className="btn-ghost" href={`/studio/${post.id}`}>Generate comments</Link>
              <a className="btn-ghost" href={post.url} target="_blank" rel="noreferrer">Open on X</a>
            </div>
          </article>
          {job ? <JobLog message={job.message} progress={job.progress} logs={job.logs} error={job.error} /> : null}
          {payload ? (
            <section className="panel grid gap-4 p-5 md:grid-cols-2">
              <Info title="Topic" body={payload.main_topic} />
              <Info title="Key message" body={payload.key_message} />
              <Info title="Audience" body={payload.audience} />
              <Info title="Discussion" body={payload.discussion_context} />
              <Info title="Engagement" body={payload.engagement_quality} />
              <Info title="Opportunity" body={payload.opportunity} />
              <Info title="Risks" body={payload.risks} />
              <Info title="Reply angle" body={payload.reply_angle} />
            </section>
          ) : <p className="text-[var(--muted)]">No analysis yet.</p>}
          {payload?.discussion && payload.discussion.length > 0 ? (
            <section className="space-y-2">
              <h2 className="text-lg">Visible replies</h2>
              {payload.discussion.map((item, index) => (
                <p key={index} className="panel p-4 text-sm"><span className="text-[var(--muted)]">@{item.handle || "unknown"} · </span>{item.text}</p>
              ))}
            </section>
          ) : null}
          {detail && detail.metrics.length > 0 ? (
            <section>
              <h2 className="text-lg">Metric history</h2>
              <ul className="mt-3 space-y-2 text-sm text-[var(--muted)]">
                {detail.metrics.map((row, index) => (
                  <li key={index}>{when(row.collected_at)} · {metric(row.views)} views · {metric(row.likes)} likes · {metric(row.replies)} replies</li>
                ))}
              </ul>
            </section>
          ) : null}
        </>
      )}
    </div>
  );
}

function Info({ title, body }: { title: string; body?: string }) {
  return (
    <div>
      <div className="label">{title}</div>
      <p>{body || "—"}</p>
    </div>
  );
}
