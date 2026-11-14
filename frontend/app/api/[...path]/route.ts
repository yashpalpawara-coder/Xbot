import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function apiBase(): string {
  const key = "API_URL";
  const url = process.env[key] || "http://127.0.0.1:8787";
  return url.replace(/\/$/, "");
}

async function cloudRunToken(audience: string): Promise<string> {
  if (!process.env.K_SERVICE) return "";
  const response = await fetch(
    `http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${encodeURIComponent(audience)}`,
    { headers: { "Metadata-Flavor": "Google" }, cache: "no-store" },
  );
  if (!response.ok) return "";
  return (await response.text()).trim();
}

function backendHeaders(request: NextRequest, token: string): Headers {
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  const accept = request.headers.get("accept");
  if (contentType) headers.set("content-type", contentType);
  if (accept) headers.set("accept", accept);
  if (token) headers.set("authorization", `Bearer ${token}`);
  return headers;
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const base = apiBase();
  const target = new URL(`${base}/api/${path.join("/")}`);
  target.search = request.nextUrl.search;
  const token = await cloudRunToken(base);
  const init: RequestInit = {
    method: request.method,
    headers: backendHeaders(request, token),
    redirect: "manual",
    cache: "no-store",
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.arrayBuffer();
  }
  const response = await fetch(target, init);
  const headers = new Headers();
  const contentType = response.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  return new NextResponse(response.body, { status: response.status, headers });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
