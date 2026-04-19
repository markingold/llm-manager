export const API_BASE = "/llm-manager-api";

export async function api(path, opts = {}) {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });

  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch (_) { }

  if (!res.ok) {
    const detail = json?.detail || json?.message || text || "";
    throw new Error(`HTTP ${res.status}${detail ? `: ${detail}` : ""}`);
  }
  return json ?? text;
}
