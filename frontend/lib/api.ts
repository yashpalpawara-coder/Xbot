export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const needsBody = method !== "GET" && method !== "HEAD" && init?.body == null;
  const response = await fetch(path, {
    ...init,
    body: needsBody ? "{}" : init?.body,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  const body = (await response.json().catch(() => ({}))) as { error?: { message?: string } };
  if (!response.ok) {
    throw new Error(body.error?.message || `Request failed (${response.status})`);
  }
  return body as T;
}

export function metric(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}

export function when(value?: string | null): string {
  if (!value) return "Date unavailable";
  const stamp = new Date(value);
  if (Number.isNaN(stamp.getTime())) return "Date unavailable";
  return stamp.toLocaleString();
}

export function weightedLength(text: string): number {
  const urls = text.match(/https?:\/\/\S+/g) ?? [];
  const rest = text.replace(/https?:\/\/\S+/g, "");
  return rest.length + urls.length * 23;
}
