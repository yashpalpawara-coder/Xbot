"use client";

import { useEffect, useState } from "react";

import { request, when } from "@/lib/api";
import type { ActivityCard, ActivitySnapshot, DailyDraft, DailyPostState, Job, VerifiedProduct } from "@/lib/types";
import { useJob } from "@/lib/use-job";

const STORAGE_KEY = "xbot-activity";
const POST_KEY = "xbot-daily-post";

const labels: Record<string, string> = {
  interested: "Interested",
  question: "Question",
  praise: "Praise",
  skeptical: "Skeptical",
  objection: "Objection",
  neutral: "Neutral",
  negative: "Negative",
  unrelated: "Unrelated",
};

export default function AnalyticsPage() {
  const [activity, setActivity] = useState<ActivitySnapshot | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [jobId, setJobId] = useState<string | null>(null);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [draftJobId, setDraftJobId] = useState<string | null>(null);
  const [postState, setPostState] = useState<DailyPostState | null>(null);
  const [writeJobId, setWriteJobId] = useState<string | null>(null);
  const [detailsJobId, setDetailsJobId] = useState<string | null>(null);
  const [publishJobId, setPublishJobId] = useState<string | null>(null);
  const live = useJob(jobId);
  const draftJob = useJob(draftJobId);
  const writeJob = useJob(writeJobId);
  const detailsJob = useJob(detailsJobId);
  const publishJob = useJob(publishJobId);

  useEffect(() => {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved) {
      try {
        const parsed = JSON.parse(saved) as ActivitySnapshot;
        if (parsed?.collected_at) setActivity(parsed);
      } catch {
        window.localStorage.removeItem(STORAGE_KEY);
      }
    }
    const storedPost = window.localStorage.getItem(POST_KEY);
    if (storedPost) {
      try {
        const parsed = JSON.parse(storedPost) as DailyPostState;
        if (parsed?.pending?.text || parsed?.post?.text) setPostState(parsed);
      } catch {
        window.localStorage.removeItem(POST_KEY);
      }
    }
    void request<DailyPostState>("/api/post/today")
      .then((saved) => {
        setPostState(saved);
        window.localStorage.setItem(POST_KEY, JSON.stringify(saved));
      })
      .catch(() => undefined);
    void request<ActivitySnapshot>("/api/activity")
      .then((snapshot) => {
        setActivity(snapshot);
        if (snapshot.collected_at) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
      })
      .catch((exc: Error) => setError(exc.message))
      .finally(() => setReady(true));
  }, []);

  useEffect(() => {
    if (live?.status === "done" || live?.status === "stopped") {
      const result = live.result as ActivitySnapshot | undefined;
      if (result?.collected_at) {
        setActivity(result);
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(result));
      } else {
        void request<ActivitySnapshot>("/api/activity")
          .then((snapshot) => {
            setActivity(snapshot);
            if (snapshot.collected_at) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
          })
          .catch(() => undefined);
      }
      setJobId(null);
    }
    if (live?.status === "error") setError(live.error || live.message);
  }, [live]);

  useEffect(() => {
    if (detailsJob?.status === "done") {
      void request<DailyPostState>("/api/post/today").then(rememberPost).catch(() => undefined);
      setDetailsJobId(null);
    }
    if (detailsJob?.status === "error") setError(detailsJob.error || detailsJob.message);
    if (detailsJob?.status === "error" || detailsJob?.status === "stopped") setDetailsJobId(null);
  }, [detailsJob]);

  useEffect(() => {
    if (writeJob?.status === "done" && writeJob.result && "text" in writeJob.result && writeJob.result.text) {
      const pending = writeJob.result as DailyDraft;
      setPostState((current) => {
        const next = { post: pending, is_today: true, count: (current?.count ?? 0) + 1, pending, details: current?.details ?? null };
        window.localStorage.setItem(POST_KEY, JSON.stringify(next));
        return next;
      });
      setWriteJobId(null);
    }
    if (writeJob?.status === "error") setError(writeJob.error || writeJob.message);
    if (writeJob?.status === "error" || writeJob?.status === "stopped") setWriteJobId(null);
  }, [writeJob]);

  useEffect(() => {
    if (publishJob?.status === "done" && publishJob.result && "text" in publishJob.result) {
      const pending = publishJob.result as DailyDraft;
      setPostState((current) => {
        const next = { post: current?.post ?? pending, is_today: true, count: current?.count ?? 1, pending, details: current?.details ?? null };
        window.localStorage.setItem(POST_KEY, JSON.stringify(next));
        return next;
      });
      setPublishJobId(null);
    }
    if (publishJob?.status === "error") setError(publishJob.error || publishJob.message);
    if (publishJob?.status === "error" || publishJob?.status === "stopped") setPublishJobId(null);
  }, [publishJob]);

  useEffect(() => {
    if (draftJob?.status === "done" || draftJob?.status === "error" || draftJob?.status === "stopped") {
      void request<ActivitySnapshot>("/api/activity").then(setActivity).catch(() => undefined);
      if (draftJob.status === "error") setError(draftJob.error || draftJob.message);
      setDraftId(null);
      setDraftJobId(null);
    }
  }, [draftJob]);

  function rememberPost(saved: DailyPostState) {
    setPostState(saved);
    window.localStorage.setItem(POST_KEY, JSON.stringify(saved));
  }

  async function gatherDetails(name: string, url: string) {
    setError(null);
    const job = await request<Job>("/api/post/details", { method: "POST", body: JSON.stringify({ name, url }) });
    setDetailsJobId(job.id);
  }

  async function reviseDetails(text: string) {
    setError(null);
    const job = await request<Job>("/api/post/details/revise", { method: "POST", body: JSON.stringify({ text }) });
    setDetailsJobId(job.id);
  }

  async function verifyDetails(name: string, url: string, detail: string) {
    setError(null);
    await request<{ product: VerifiedProduct }>("/api/post/details/verify", {
      method: "POST",
      body: JSON.stringify({ name, url, detail }),
    });
    rememberPost(await request<DailyPostState>("/api/post/today"));
  }

  async function writePost(text: string) {
    setError(null);
    const job = await request<Job>("/api/post/today", { method: "POST", body: JSON.stringify({ text }) });
    setWriteJobId(job.id);
  }

  async function approvePost(text: string) {
    setError(null);
    const job = await request<Job>("/api/post/approve", { method: "POST", body: JSON.stringify({ text }) });
    setPublishJobId(job.id);
  }

  async function refresh() {
    setError(null);
    setFilter("all");
    const job = await request<Job>("/api/activity/scan", { method: "POST" });
    setJobId(job.id);
  }

  async function draft(id: string) {
    setError(null);
    setDraftId(id);
    const job = await request<Job>(`/api/activity/responses/${id}/draft`, { method: "POST" });
    setDraftJobId(job.id);
  }

  const busy = live?.status === "running" || Boolean(jobId && !live);
  const posts = activity?.posts ?? [];
  const comments = activity?.comments ?? [];
  const responses = activity?.responses ?? [];
  const categories = activity?.categories ?? [];
  const hasScan = Boolean(activity?.collected_at);
  const visible = filter === "all" ? responses : responses.filter((item) => item.category === filter);
  const replyTotal = categories.reduce((sum, item) => sum + item.count, 0) || responses.length;
  const status = live?.message || (hasScan ? "Saved in app storage" : "Ready");

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm uppercase tracking-[0.16em] text-[var(--gold)]">Analysis</p>
          <h1 className="mt-2 font-[family-name:var(--font-serif)] text-5xl font-medium tracking-tight">What came back.</h1>
          <p className="mt-3 max-w-xl text-sm text-[var(--muted)]">
            Posts you wrote, comments you left, and the replies under them. A draft stays on this page. It is not posted.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {busy ? (
            <button className="btn" type="button" onClick={() => jobId && void request(`/api/jobs/${jobId}/stop`, { method: "POST" })}>
              Stop
            </button>
          ) : (
            <button className="btn" type="button" onClick={() => void refresh().catch((exc: Error) => setError(exc.message))}>
              Refresh activity
            </button>
          )}
        </div>
      </header>

      <p className="text-sm text-[var(--muted)]">{detailsJob?.message || writeJob?.message || publishJob?.message || status}</p>
      <WritePost
        state={postState}
        detailing={detailsJob?.status === "running" || Boolean(detailsJobId && !detailsJob)}
        writing={writeJob?.status === "running" || Boolean(writeJobId && !writeJob)}
        publishing={publishJob?.status === "running" || Boolean(publishJobId && !publishJob)}
        onDetails={(name, url) => void gatherDetails(name, url).catch((exc: Error) => setError(exc.message))}
        onRevise={(text) => void reviseDetails(text).catch((exc: Error) => setError(exc.message))}
        onVerify={(name, url, detail) => void verifyDetails(name, url, detail).catch((exc: Error) => setError(exc.message))}
        onWrite={(text) => void writePost(text).catch((exc: Error) => setError(exc.message))}
        onApprove={(text) => void approvePost(text).catch((exc: Error) => setError(exc.message))}
        onStop={() => {
          if (detailsJobId) void request(`/api/jobs/${detailsJobId}/stop`, { method: "POST" });
          if (writeJobId) void request(`/api/jobs/${writeJobId}/stop`, { method: "POST" });
          if (publishJobId) void request(`/api/jobs/${publishJobId}/stop`, { method: "POST" });
        }}
      />
      {error ? <p className="text-[var(--bad)]">{error}</p> : null}
      {!ready && !hasScan ? <p className="text-sm text-[var(--muted)]">Loading the last scan.</p> : null}

      {ready && !hasScan && !busy ? (
        <section className="panel p-8 text-center">
          <p className="font-[family-name:var(--font-serif)] text-3xl">No scan yet.</p>
          <p className="mx-auto mt-3 max-w-md text-sm text-[var(--muted)]">
            Sign in on X, then refresh. The scan reads your profile and the replies under those posts. It does not reply.
          </p>
        </section>
      ) : null}

      {hasScan || busy ? (
        <>
          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Stat label="Posts" value={activity?.posts_created ?? 0} detail="you wrote" />
            <Stat label="Comments" value={activity?.comments_dropped ?? 0} detail="you left" />
            <Stat label="Views" value={activity?.views ?? 0} detail="every post and reply" />
            <Stat label="Likes" value={activity?.likes ?? 0} detail="every post and reply" />
            <Stat label="Replies" value={activity?.replies_received ?? 0} detail="people wrote back" />
          </section>
          <p className="text-sm text-[var(--muted)]">
            {activity?.handle ? `@${activity.handle}` : "Your profile"}
            {activity?.collected_at ? ` · ${when(activity.collected_at)}` : ""}
            {activity?.note ? ` · ${activity.note}` : ""}
          </p>

          <section className="panel space-y-4 p-5">
            <p className="text-xs uppercase tracking-[0.16em] text-[var(--muted)]">Response types</p>
            <p className="font-[family-name:var(--font-serif)] text-2xl">{activity?.summary || (busy ? "Replies show up here once the scan reads them." : "No reply mix yet.")}</p>
            {categories.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                <Filter active={filter === "all"} onClick={() => setFilter("all")}>All · {responses.length}</Filter>
                {categories.map((category) => (
                  <Filter key={category.name} active={filter === category.name} onClick={() => setFilter(category.name)}>
                    {labels[category.name] || category.name} · {category.count}
                    {replyTotal ? ` · ${Math.round((category.count / replyTotal) * 100)}%` : ""}
                  </Filter>
                ))}
              </div>
            ) : null}
            <div className="space-y-3">
              {visible.map((reply) => (
                <ResponseCard key={reply.id} reply={reply} drafting={draftId === reply.id} onDraft={(id) => void draft(id).catch((exc: Error) => setError(exc.message))} />
              ))}
            </div>
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            <Feed title="Recent posts" empty="No posts on this account yet." items={posts} />
            <Feed title="Recent comments" empty="No comments on this account yet." items={comments} />
          </div>
          {live?.logs && live.logs.length > 0 ? (
            <ol className="panel max-h-40 space-y-2 overflow-auto p-4 text-sm text-[var(--muted)]">
              {live.logs.map((line, index) => (
                <li key={`${index}-${line.slice(0, 24)}`}>{line}</li>
              ))}
            </ol>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function WritePost({
  state,
  detailing,
  writing,
  publishing,
  onDetails,
  onRevise,
  onVerify,
  onWrite,
  onApprove,
  onStop,
}: {
  state: DailyPostState | null;
  detailing: boolean;
  writing: boolean;
  publishing: boolean;
  onDetails: (name: string, url: string) => void;
  onRevise: (text: string) => void;
  onVerify: (name: string, url: string, detail: string) => void;
  onWrite: (text: string) => void;
  onApprove: (text: string) => void;
  onStop: () => void;
}) {
  const details = state?.details;
  const [name, setName] = useState(details?.name ?? "");
  const [url, setUrl] = useState(details?.url ?? "");
  const [changeOpen, setChangeOpen] = useState(false);
  const [change, setChange] = useState("");
  const [note, setNote] = useState("");
  const draft = state?.pending?.text ? state.pending : state?.post;
  const writeup = details?.writeup ?? "";
  const verified = Boolean(details?.verified && writeup);
  const busy = detailing || writing || publishing;
  useEffect(() => {
    if (details?.name) setName(details.name);
    if (details?.url) setUrl(details.url);
  }, [details?.name, details?.url]);
  return (
    <section className="panel space-y-5 p-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.16em] text-[var(--muted)]">Product</p>
          <h2 className="mt-2 font-[family-name:var(--font-serif)] text-3xl">Name and URL first</h2>
          <p className="mt-2 max-w-2xl text-sm text-[var(--muted)]">
            ChatGPT reads the page and writes what the product is, its features, who it is for, and what to use when promoting it. Nothing is saved until you verify it.
          </p>
        </div>
        {busy ? <button className="btn" type="button" onClick={onStop}>Stop</button> : null}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="text-[var(--muted)]">Name</span>
          <input className="field mt-2" value={name} onChange={(event) => setName(event.target.value)} placeholder="Product name" />
        </label>
        <label className="block text-sm">
          <span className="text-[var(--muted)]">URL</span>
          <input className="field mt-2" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com" />
        </label>
      </div>
      <button className="btn" type="button" disabled={busy || !name.trim() || !url.trim()} onClick={() => onDetails(name.trim(), url.trim())}>
        {detailing && !changeOpen ? "Asking ChatGPT" : "Analyze"}
      </button>
      {writeup ? (
        <div>
          <h3 className="font-[family-name:var(--font-serif)] text-2xl">Output</h3>
          <div className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap rounded-2xl border border-[var(--line)] p-3 text-sm leading-relaxed">{writeup}</div>
          {verified ? (
            <p className="mt-3 text-sm text-[var(--muted)]">Verified and saved. {details?.name} · {details?.url}</p>
          ) : (
            <div className="mt-3 flex flex-wrap gap-3">
              <button className="btn-ghost" type="button" disabled={busy} onClick={() => setChangeOpen(true)}>Need to change</button>
              <button className="btn" type="button" disabled={busy || !name.trim() || !url.trim()} onClick={() => onVerify(name.trim(), url.trim(), writeup)}>Verify</button>
            </div>
          )}
          {changeOpen && !verified ? (
            <div className="mt-3">
              <textarea
                className="min-h-24 w-full rounded-2xl border border-[var(--line)] bg-transparent px-3 py-2"
                placeholder="What should change?"
                value={change}
                onChange={(event) => setChange(event.target.value)}
              />
              <button className="btn mt-3" type="button" disabled={busy || !change.trim()} onClick={() => onRevise(change.trim())}>
                {detailing ? "Asking ChatGPT" : "Generate again"}
              </button>
            </div>
          ) : null}
        </div>
      ) : (
        <p className="text-sm text-[var(--muted)]">No analysis yet. Enter a name and URL, then Analyze.</p>
      )}
      {verified ? (
      <div>
        <h3 className="font-[family-name:var(--font-serif)] text-2xl">Your note</h3>
        <textarea
          className="mt-3 min-h-24 w-full rounded-2xl border border-[var(--line)] bg-transparent px-3 py-2"
          placeholder="What should this post say?"
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
        <button className="btn mt-3" type="button" disabled={busy || !note.trim()} onClick={() => onWrite(note)}>
          {writing ? "Writing" : "Write the post"}
        </button>
      {draft?.text ? (
        <article className="mt-4 rounded-2xl border border-[var(--line)] p-4">
          <p className="whitespace-pre-wrap leading-relaxed">{draft.text}</p>
          <p className="mt-3 text-sm text-[var(--muted)]">{draft.posted ? "Posted." : "Saved in app storage. Nothing is posted until you approve it."}</p>
          <div className="mt-3 flex flex-wrap gap-3 text-sm">
            <button type="button" className="text-[var(--gold)]" onClick={() => void navigator.clipboard.writeText(draft.text)}>Copy</button>
            {draft.intent_url ? <a className="text-[var(--gold)]" href={draft.intent_url} target="_blank" rel="noreferrer">Open composer</a> : null}
            {draft.posted && draft.posted_url ? <a className="text-[var(--gold)]" href={draft.posted_url} target="_blank" rel="noreferrer">Open on X</a> : null}
            {!draft.posted ? (
              <button type="button" className="btn" disabled={busy} onClick={() => onApprove(draft.text)}>Approve and post</button>
            ) : null}
          </div>
        </article>
      ) : (
        <p className="mt-4 text-sm text-[var(--muted)]">No post yet. Type a note, then Write the post. The draft shows here, and nothing is posted until you approve it.</p>
      )}
      </div>
      ) : null}
    </section>
  );
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

function Feed({ title, empty, items }: { title: string; empty: string; items: ActivityCard[] }) {
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState(items[0]?.id ?? "");
  const selected = items.find((item) => item.id === picked) ?? items[0];

  return (
    <section className="panel p-5">
      <button type="button" className="flex w-full items-baseline justify-between gap-3 text-left" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <h2 className="font-[family-name:var(--font-serif)] text-2xl">{title}</h2>
        <span className="text-sm text-[var(--muted)]">{items.length} {open ? "▴" : "▾"}</span>
      </button>
      {open && items.length === 0 ? <p className="mt-4 text-sm text-[var(--muted)]">{empty}</p> : null}
      {open && selected ? (
        <div className="mt-4 space-y-3">
          <label className="block text-sm text-[var(--muted)]">
            Stored here
            <select
              className="mt-2 w-full rounded-2xl border border-[var(--line)] bg-transparent px-3 py-2 text-[var(--ink)]"
              value={selected.id}
              onChange={(event) => setPicked(event.target.value)}
            >
              {items.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.reply_to ? `@${item.reply_to} · ` : ""}
                  {item.text.replace(/\s+/g, " ").slice(0, 90)}
                </option>
              ))}
            </select>
          </label>
          <article className="rounded-2xl border border-[var(--line)] p-4">
            {selected.reply_to ? <p className="text-xs uppercase text-[var(--gold)]">Replying to @{selected.reply_to}</p> : null}
            <p className="leading-relaxed">{selected.text}</p>
            <p className="mt-3 text-sm text-[var(--muted)]">
              {(selected.views ?? 0).toLocaleString()} views · {(selected.likes ?? 0).toLocaleString()} likes · {(selected.replies ?? 0).toLocaleString()} replies
              {selected.created_at ? ` · ${when(selected.created_at)}` : ""}
            </p>
            <a className="mt-3 inline-flex text-sm text-[var(--gold)]" href={selected.url} target="_blank" rel="noreferrer">Open on X</a>
          </article>
        </div>
      ) : null}
    </section>
  );
}

function ResponseCard({ reply, drafting, onDraft }: { reply: ActivityCard; drafting: boolean; onDraft: (id: string) => void }) {
  return (
    <article className="rounded-2xl border border-[var(--line)] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3>@{reply.handle}</h3>
        <span className="text-xs uppercase text-[var(--muted)]">{labels[reply.category || ""] || "Unsorted"}</span>
      </div>
      <p className="mt-2 leading-relaxed">{reply.text}</p>
      {reply.why ? <p className="mt-2 text-sm text-[var(--muted)]">{reply.why}</p> : null}
      {reply.parent_text ? <p className="mt-2 text-sm text-[var(--muted)]">Under: {reply.parent_text}</p> : null}
      <div className="mt-3 flex flex-wrap gap-3 text-sm">
        <a className="text-[var(--gold)]" href={reply.url} target="_blank" rel="noreferrer">Open reply</a>
        <button type="button" disabled={drafting} onClick={() => onDraft(reply.id)}>{drafting ? "Drafting" : "Draft reply"}</button>
      </div>
      {reply.draft?.text ? (
        <div className="mt-3 rounded-2xl border border-[var(--line)] p-3">
          <p className="text-xs uppercase text-[var(--muted)]">Draft only. Nothing was posted.</p>
          <p className="mt-2">{reply.draft.text}</p>
        </div>
      ) : reply.draft?.status === "skipped" ? (
        <p className="mt-3 text-sm text-[var(--muted)]">No draft. {reply.draft.reason || "Skipped."} Nothing was posted.</p>
      ) : null}
    </article>
  );
}

function Filter({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} className={`rounded-full border px-3 py-1 text-sm ${active ? "border-[var(--gold)] text-[var(--gold)]" : "border-[var(--line)] text-[var(--muted)]"}`}>
      {children}
    </button>
  );
}
