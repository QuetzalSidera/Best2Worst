const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/backend";

export async function apiGet(path) {
  const response = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed`);
  }
  return response.json();
}

export async function apiPost(path, body, init = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    body: body instanceof FormData ? body : JSON.stringify(body || {}),
    ...init,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `POST ${path} failed`);
  }
  return payload;
}

export async function fetchBootstrap() {
  return apiGet("/api/bootstrap");
}
