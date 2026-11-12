"use client";

import { FormEvent, useEffect, useState } from "react";

import { createClient } from "@/lib/supabase/client";
import { request } from "@/lib/api";
import type { Health, Settings } from "@/lib/types";

export function SettingsScreen({ supabase }: { supabase: { ok: boolean; detail: string } }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [browserCount, setBrowserCount] = useState<string>("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [xPassword, setXPassword] = useState("");

  useEffect(() => {
    void request<{ settings: Settings }>("/api/settings").then((body) => setSettings(body.settings)).catch((exc: Error) => setError(exc.message));
    void request<Health>("/api/health").then(setHealth).catch(() => undefined);
    const client = createClient();
    void client.from("posts").select("id").limit(1).then(({ data, error: readError }) => {
      setBrowserCount(readError ? readError.message : `${data?.length ?? 0} recent posts visible in the browser`);
    });
  }, []);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!settings) return;
    setError(null);
    try {
      const body = await request<{ settings: Settings }>("/api/settings", {
        method: "PUT",
        body: JSON.stringify({ ...settings, x_password: xPassword }),
      });
      setSettings(body.settings);
      setXPassword("");
      setMessage("Saved.");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not save");
    }
  }

  async function download() {
    const data = await request<unknown>("/api/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "xbot-export.json";
    link.click();
    URL.revokeObjectURL(url);
  }

  if (!settings) return <p className="text-[var(--muted)]">Loading settings…</p>;

  return (
    <div className="space-y-6">
      <header>
        <p className="text-sm uppercase tracking-[0.16em] text-[var(--gold)]">Settings</p>
        <h1 className="mt-2 font-[family-name:var(--font-serif)] text-5xl font-medium tracking-tight">Sessions and limits.</h1>
      </header>
      {error ? <p className="text-[var(--bad)]">{error}</p> : null}
      {message ? <p className="text-[var(--good)]">{message}</p> : null}
      <section className="panel space-y-2 p-5 text-sm text-[var(--muted)]">
        <p>Backend database: {health?.database || "…"} — {health?.database_detail}</p>
        <p>Browser key: {supabase.ok ? "connected" : "not ready"} — {supabase.detail}</p>
        <p>Direct read: {browserCount || "checking…"}</p>
        <p>Chrome profile: {health?.browser_profile}</p>
        <p>Run supabase/schema.sql in the Supabase SQL editor if the tables are missing. The X password is saved on this computer and is not shown again.</p>
      </section>
      <form className="panel grid gap-4 p-5" onSubmit={onSubmit}>
        <label className="block"><span className="label">X username or email</span>
          <input className="field" autoComplete="username" value={settings.x_username || ""} onChange={(event) => setSettings({ ...settings, x_username: event.target.value })} placeholder="name or email" />
        </label>
        <label className="block"><span className="label">X password</span>
          <input className="field" type="password" autoComplete="current-password" value={xPassword} onChange={(event) => setXPassword(event.target.value)} placeholder={settings.x_password_set ? "Saved. Type a new one to replace it." : "Password"} />
        </label>
        <p className="text-sm text-[var(--muted)]">Used when X is signed out.</p>
        <label className="block"><span className="label">Product name</span>
          <input className="field" value={settings.product_name} onChange={(event) => setSettings({ ...settings, product_name: event.target.value })} />
        </label>
        <label className="block"><span className="label">Product URL</span>
          <input className="field" value={settings.product_url} onChange={(event) => setSettings({ ...settings, product_url: event.target.value })} />
        </label>
        <label className="block"><span className="label">Product facts</span>
          <textarea className="area" value={settings.product_pitch} onChange={(event) => setSettings({ ...settings, product_pitch: event.target.value })} />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={settings.allow_promotion} onChange={(event) => setSettings({ ...settings, allow_promotion: event.target.checked })} />
          Allow product mentions by default
        </label>
        <label className="block"><span className="label">Search limit</span>
          <input className="field" type="number" min={1} max={40} value={settings.search_limit} onChange={(event) => setSettings({ ...settings, search_limit: Number(event.target.value) })} />
        </label>
        <div className="flex flex-wrap gap-2">
          <button className="btn" type="submit">Save settings</button>
          <button className="btn-ghost" type="button" onClick={download}>Export JSON</button>
        </div>
      </form>
    </div>
  );
}
