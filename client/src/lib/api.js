const API_BASE = "/api";

async function request(path, params = {}) {
  const entries = Object.entries(params).filter(([, v]) => v != null);
  const query = new URLSearchParams(entries).toString();
  const url = query ? `${path}?${query}` : path;

  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

async function requestJson(path, method = "POST", body = null) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body == null ? undefined : JSON.stringify(body),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export function fetchKpis({ startDate, endDate } = {}) {
  return request(`${API_BASE}/kpis/dashboard`, {
    start_date: startDate,
    end_date: endDate,
  });
}

export function fetchUnifiedData({ startDate, endDate } = {}) {
  return request(`${API_BASE}/data/live/unified`, {
    start_date: startDate,
    end_date: endDate,
  });
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

export function startScheduler(startTime) {
  return requestJson(`${API_BASE}/scheduler/start`, "POST", {
    start_time: startTime,
  });
}

export function stopSchedulerApi() {
  return requestJson(`${API_BASE}/scheduler/stop`, "POST");
}
