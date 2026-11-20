"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { JobLog, PostCard } from "@/components/post-card";
import { request } from "@/lib/api";
import type { ComingPost, DailyPostState, Job, Post, Settings, VerifiedProduct } from "@/lib/types";
import { useJob } from "@/lib/use-job";

function FinderScreen() {
  const params = useSearchParams();
  const [topic, setTopic] = useState(params.get("topic") || "");
  const [keywords, setKeywords] = useState("");
  const [hashtags, setHashtags] = useState("");
  const [handles, setHandles] = useState("");
  const [url, setUrl] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [minViews, setMinViews] = useState("");
  const [minLikes, setMinLikes] = useState("");
  const [language, setLanguage] = useState("");
  const [sort, setSort] = useState("relevant");
  const [jobId, setJobId] = useState<string | null>(null);
  const [posts, setPosts] = useState<Post[]>([]);
  const [error, setError] = useState<string | null>(null);
  const job = useJob(jobId);

  useEffect(() => {
    if (job?.result?.posts) setPosts(job.result.posts);
  }, [job]);

  const ordered = useMemo(() => {
    const copy = [...posts];
    copy.sort((left, right) => {
      if (sort === "likes") return (right.latest_likes ?? -1) - (left.latest_likes ?? -1);
      if (sort === "views") return (right.latest_views ?? -1) - (left.latest_views ?? -1);
      if (sort === "recent") return (right.created_at || "").localeCompare(left.created_at || "");
      return (right.relevance ?? 0) - (left.relevance ?? 0);
    });
    return copy;
  }, [posts, sort]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const started = await request<Job>("/api/finder/search", {
        method: "POST",
        body: JSON.stringify({
          topic,
          keywords,
          hashtags,
          handles,
          url,
          since,
          until,
          language,
          sort,
          min_views: minViews ? Number(minViews) : null,
          min_likes: minLikes ? Number(minLikes) : null,
          limit: 20,
        }),
      });
      setJobId(started.id);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Search failed");
    }
  }

  async function stop() {
    if (!jobId) return;
    await request(`/api/jobs/${jobId}/stop`, { method: "POST" });
  }

  return (
    <div className="space-y-6">
      <header>
        <p className="text-sm uppercase tracking-[0.16em] text-[var(--gold)]">Finder</p>
        <h1 className="mt-2 font-[family-name:var(--font-serif)] text-5xl font-medium tracking-tight">Search public posts.</h1>
      </header>
      <ProductFind />
      <form className="panel grid gap-4 p-5 md:grid-cols-2" onSubmit={onSubmit}>
        <Field label="Topic" value={topic} onChange={setTopic} />
        <Field label="Keywords" value={keywords} onChange={setKeywords} placeholder="automation, agents" />
        <Field label="Hashtags" value={hashtags} onChange={setHashtags} placeholder="buildinpublic" />
        <Field label="Creators" value={handles} onChange={setHandles} placeholder="@handle" />
        <Field label="Post URL" value={url} onChange={setUrl} placeholder="https://x.com/.../status/..." />
        <label className="block">
          <span className="label">Language</span>
          <select className="select" value={language} onChange={(event) => setLanguage(event.target.value)}>
            <option value="">Any</option>
            <option value="en">English</option>
            <option value="fr">French</option>
            <option value="ja">Japanese</option>
          </select>
        </label>
        <Field label="Since" value={since} onChange={setSince} placeholder="2026-11-01" />
        <Field label="Until" value={until} onChange={setUntil} placeholder="2026-11-06" />
        <Field label="Minimum views" value={minViews} onChange={setMinViews} />
        <Field label="Minimum likes" value={minLikes} onChange={setMinLikes} />
        <div className="md:col-span-2 flex flex-wrap gap-2">
          <button className="btn" type="submit" disabled={job?.status === "running"}>Start search</button>
          <button className="btn-danger" type="button" onClick={stop} disabled={!jobId || job?.status !== "running"}>Stop</button>
        </div>
      </form>
      {error ? <p className="text-[var(--bad)]">{error}</p> : null}
      {job ? <JobLog message={job.message} progress={job.progress} logs={job.logs} error={job.error} /> : null}
      <div className="flex items-center justify-between">
        <h2 className="text-lg">{ordered.length} posts</h2>
        <select className="select w-auto" value={sort} onChange={(event) => setSort(event.target.value)}>
          <option value="relevant">Relevance</option>
          <option value="likes">Likes</option>
          <option value="views">Views</option>
          <option value="recent">Recent</option>
        </select>
      </div>
      <div className="space-y-3">
        {ordered.map((post) => <PostCard key={post.id} post={post} />)}
      </div>
    </div>
  );
}

function ProductFind() {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [detail, setDetail] = useState("");
  const [savedDetail, setSavedDetail] = useState("");
  const [finderVerified, setFinderVerified] = useState(false);
  const [changing, setChanging] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [change, setChange] = useState("");
  const [coming, setComing] = useState<ComingPost[]>([]);
  const [listName, setListName] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [batch, setBatch] = useState("20");
  const [queries, setQueries] = useState<string[]>([]);
  const [creators, setCreators] = useState<string[]>([]);
  const [country, setCountry] = useState("All");
  const job = useJob(jobId);
  const ready = finderVerified && detail.trim() !== "" && detail.trim() === savedDetail.trim() && !reviewing;
  const countryCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const post of coming) {
      const name = post.country?.trim() || "Unknown";
      counts.set(name, (counts.get(name) || 0) + 1);
    }
    return [...counts.entries()].sort((left, right) => {
      if (left[0] === "Unknown") return 1;
      if (right[0] === "Unknown") return -1;
      return right[1] - left[1] || left[0].localeCompare(right[0]);
    });
  }, [coming]);
  const shown = country === "All" ? coming : coming.filter((post) => (post.country?.trim() || "Unknown") === country);
  const revising = job?.type === "post_details" && (job.status === "running" || Boolean(jobId && !job));

  async function load() {
    const [productBody, today, list, settingsBody] = await Promise.all([
      request<{ product: VerifiedProduct | null }>("/api/product"),
      request<DailyPostState>("/api/post/today"),
      request<{ posts: ComingPost[]; product_name?: string }>("/api/finder/coming"),
      request<{ settings: Settings }>("/api/settings"),
    ]);
    const product = productBody.product;
    const writeup = today.details?.writeup?.trim() || "";
    const dailyName = today.details?.name?.trim() || "";
    const sameProduct = !product?.name || !dailyName || dailyName === product.name;
    const text = (sameProduct ? writeup : "") || product?.detail || "";
    setDetail(text);
    setName((sameProduct ? today.details?.name : "") || product?.name || settingsBody.settings.product_name || "");
    setUrl((sameProduct ? today.details?.url : "") || product?.url || settingsBody.settings.product_url || "");
    setSavedDetail(product?.detail || "");
    setFinderVerified(Boolean(product?.finder_verified));
    setComing(list.posts || []);
    setListName(list.product_name || product?.name || "");
    setQueries(product?.search_queries || []);
    setCreators(product?.creators || []);
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
    const next = job?.result?.queries;
    if (next && next.length > 0) setQueries(next);
    const foundCreators = job?.result?.creators;
    if (foundCreators && foundCreators.length > 0) setCreators(foundCreators);
  }, [job]);

  useEffect(() => {
    if (!job) return;
    void request<{ posts: ComingPost[]; product_name?: string }>("/api/finder/coming")
      .then((list) => {
        setComing(list.posts || []);
        if (list.product_name) setListName(list.product_name);
      })
      .catch(() => undefined);
  }, [job]);

  async function generateAgain() {
    const note = change.trim();
    if (!note) return;
    setBusy(true);
    setError(null);
    try {
      const started = await request<Job>("/api/post/details/revise", {
        method: "POST",
        body: JSON.stringify({ text: note }),
      });
      setJobId(started.id);
      setReviewing(true);
      setChanging(false);
      setChange("");
    } catch (exc) {
      setReviewing(false);
      setError(exc instanceof Error ? exc.message : "Could not change the details.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (job?.type !== "post_details") return;
    if (job.status === "done") {
      setReviewing(true);
      void load();
    }
    if (job.status === "error" || job.status === "stopped") setReviewing(false);
  }, [job]);

  async function verify() {
    setBusy(true);
    setError(null);
    try {
      await request("/api/post/details/verify", {
        method: "POST",
        body: JSON.stringify({ name, url, detail, for_finder: true }),
      });
      setReviewing(false);
      await load();
      window.dispatchEvent(new Event("xbot-product"));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not verify.");
    } finally {
      setBusy(false);
    }
  }

  async function findPosts() {
    setError(null);
    try {
      const count = Number(batch);
      const started = await request<Job>("/api/finder/grok", {
        method: "POST",
        body: JSON.stringify({ count }),
      });
      setJobId(started.id);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not start Grok.");
    }
  }

  async function stop() {
    if (!jobId) return;
    await request(`/api/jobs/${jobId}/stop`, { method: "POST" });
  }

  return (
    <section className="panel space-y-4 p-5">
      <div>
        <h2 className="text-lg">Saved product</h2>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <label className="block">
          <span className="label">Name</span>
          <input className="field" value={name} onChange={(event) => setName(event.target.value)} placeholder="Product name" />
        </label>
        <label className="block">
          <span className="label">Product URL</span>
          <input className="field" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com" />
        </label>
      </div>
      {detail ? (
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-2xl bg-[var(--paper)] p-4 text-sm leading-6">{detail}</pre>
      ) : (
        <p className="text-sm text-[var(--muted)]">Verify a product on Analysis first. It will show here.</p>
      )}
      {detail ? (
        <div className="flex flex-wrap gap-2">
          <button className="btn-ghost" type="button" onClick={() => setChanging((open) => !open)} disabled={revising}>Need to change</button>
          {!ready ? (
            <button className="btn" type="button" onClick={() => void verify()} disabled={busy || revising || !name.trim() || !url.trim()}>Verify</button>
          ) : null}
        </div>
      ) : null}
      {!ready && detail ? <p className="text-sm text-[var(--muted)]">Verify saves this analysis and replaces the previous one.</p> : null}
      {changing ? (
        <div className="space-y-2">
          <label className="block">
            <span className="label">What should change?</span>
            <textarea className="field min-h-24" value={change} onChange={(event) => setChange(event.target.value)} />
          </label>
          <button className="btn" type="button" onClick={() => void generateAgain()} disabled={busy || !change.trim()}>Generate again</button>
        </div>
      ) : null}
      {ready ? <p className="text-sm">Verified for Finder. Find is ready.</p> : null}
      <div className="flex flex-wrap items-end gap-2">
        <label className="block">
          <span className="label">Posts each ask</span>
          <input
            className="field w-24"
            type="number"
            min={1}
            max={100}
            value={batch}
            onChange={(event) => setBatch(event.target.value)}
          />
        </label>
        <button className="btn" type="button" onClick={() => void findPosts()} disabled={!ready || !Number.isInteger(Number(batch)) || Number(batch) < 1 || Number(batch) > 100 || job?.status === "running"}>Find</button>
        <button className="btn-danger" type="button" onClick={() => void stop()} disabled={job?.type !== "grok_find" || job.status !== "running"}>Stop</button>
      </div>
      {error ? <p className="text-[var(--bad)]">{error}</p> : null}
      {job?.type === "grok_find" || job?.type === "post_details" ? (
        <JobLog message={job.message} progress={job.progress} logs={job.logs} error={job.error} />
      ) : null}
      {creators.length > 0 ? (
        <div>
          <h3 className="text-lg">Saved creators</h3>
          <ol className="mt-2 max-h-48 list-decimal space-y-1 overflow-auto pl-5 text-sm text-[var(--muted)]">
            {creators.map((creator) => (
              <li key={creator}>@{creator}</li>
            ))}
          </ol>
        </div>
      ) : null}
      {queries.length > 0 ? (
        <div>
          <h3 className="text-lg">Saved search queries</h3>
          <ol className="mt-2 max-h-48 list-decimal space-y-1 overflow-auto pl-5 text-sm text-[var(--muted)]">
            {queries.map((query) => (
              <li key={query}>{query}</li>
            ))}
          </ol>
        </div>
      ) : null}
      <div>
        <div className="flex items-center justify-between">
          <h3 className="text-lg">{listName ? `${listName} posts` : "Coming posts"}</h3>
          <a className="text-sm text-[var(--gold)]" href="/coming">Open the coming panel</a>
        </div>
        {coming.length === 0 ? <p className="mt-2 text-sm text-[var(--muted)]">{listName ? `None yet for ${listName}.` : "None yet."}</p> : null}
        {coming.length > 0 ? (
          <div className="mt-3">
            <label className="block max-w-xs">
              <span className="label">Country</span>
              <select className="select" value={countryCounts.some(([name]) => name === country) ? country : "All"} onChange={(event) => setCountry(event.target.value)}>
                <option value="All">All countries</option>
                {countryCounts.map(([name]) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </label>
            <p className="mt-2 text-sm text-[var(--muted)]">Country is the place on the post, the Account based in label, or a profile location that names a country. Otherwise the post is Unknown.</p>
            <table className="mt-3 w-full text-sm">
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
          </div>
        ) : null}
        <ul className="mt-2 space-y-2">
          {shown.map((post) => (
            <li key={post.id} className="rounded-2xl bg-[var(--paper)] p-3 text-sm">
              <a className="break-all text-[var(--gold)]" href={post.url} target="_blank" rel="noreferrer">{post.url}</a>
              <span className="mt-1 block text-[var(--muted)]">{post.country?.trim() || "Unknown"}</span>
              {post.views ? <span className="mt-1 block text-[var(--muted)]">{post.views.toLocaleString()} views</span> : null}
              {post.note ? <span className="mt-1 block text-[var(--muted)]">{post.note}</span> : null}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function Field({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (value: string) => void; placeholder?: string }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <input className="field" value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

export default function FinderPage() {
  return (
    <Suspense fallback={<p className="text-[var(--muted)]">Loading Finder…</p>}>
      <FinderScreen />
    </Suspense>
  );
}
