// Use environment variable for the base URL, falling back to local dev if not set
const API_BASE = import.meta.env.VITE_API_URL 
    ? `https://${import.meta.env.VITE_API_URL}/api`
    : "http://localhost:8000/api";

// 🚀 The master fetch wrapper that attaches the secure cookie
async function authFetch(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    credentials: "include", // 🚨 CRITICAL: Sends the HttpOnly session cookie
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });

  // If the backend rejects the cookie (expired/invalid), force a re-login
  if (response.status === 401) {
    console.warn("Session expired or invalid. Redirecting to login...");
    window.location.reload();
  }

  return response;
}

async function request(path, params = {}) {
  const entries = Object.entries(params).filter(([, v]) => v != null);
  const query = new URLSearchParams(entries).toString();
  const url = query ? `${path}?${query}` : path;

  const res = await authFetch(url); // Use authFetch
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

async function requestJson(path, method = "POST", body = null) {
  const res = await authFetch(path, { // Use authFetch
    method,
    body: body == null ? undefined : JSON.stringify(body),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

// --- Your existing exported functions remain exactly the same! ---
export function fetchKpis({ startDate, endDate } = {}) {
  return request(`${API_BASE}/kpis/dashboard`, { start_date: startDate, end_date: endDate });
}

export function fetchUnifiedData({ startDate, endDate } = {}) {
  return request(`${API_BASE}/data/live/unified`, { start_date: startDate, end_date: endDate });
}

export async function sendTestEmail() {
  return requestJson(`${API_BASE}/mail/send-daily-report`, "POST");
}

export function fetchSchedulerConfig() {
  return request(`${API_BASE}/scheduler/config`);
}

export function updateSchedulerConfig(payload) {
  return requestJson(`${API_BASE}/scheduler/config`, "POST", payload);
}

export function fetchSchedulerStatus() {
  return request(`${API_BASE}/scheduler/status`);
}

export async function fetchSchedulerHistory() {
  try {
    return await request(`${API_BASE}/scheduler/history`);
  } catch {
    const fallbackUrl = "http://127.0.0.1:8000/api/scheduler/history";
    const res = await fetch(fallbackUrl);
    if (!res.ok) {
      throw new Error(`Request failed: ${res.status}`);
    }
    return res.json();
  }
}

export function startScheduler(startTime) {
  return requestJson(`${API_BASE}/scheduler/start`, "POST", { start_time: startTime });
}

export function stopSchedulerApi() {
  return requestJson(`${API_BASE}/scheduler/stop`, "POST");
}