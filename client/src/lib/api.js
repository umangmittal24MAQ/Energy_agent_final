const isLocalDevHost = /^(localhost|127\.0\.0\.1)$/i.test(
  window.location.hostname,
);
const isLoopbackHost = (host) => /^(localhost|127\.0\.0\.1)$/i.test(host);
const DEFAULT_LOCAL_API_ORIGIN = isLocalDevHost
  ? `${window.location.protocol}//${window.location.hostname}:8000`
  : "http://localhost:8000";

function toOrigin(value, fallbackProtocol = "https") {
  if (!value) return null;
  const withProtocol = /^https?:\/\//i.test(value)
    ? value
    : `${fallbackProtocol}://${value}`;
  try {
    return new URL(withProtocol).origin;
  } catch {
    return null;
  }
}

export const BACKEND_ORIGIN = (() => {
  const explicitBase = import.meta.env.VITE_API_BASE_URL;

  // In local development, keep requests same-origin so Vite proxy can forward
  // to the backend and browser session cookies remain stable.
  if (isLocalDevHost) {
    if (explicitBase) {
      try {
        const explicitUrl = new URL(explicitBase);
        if (isLoopbackHost(explicitUrl.hostname)) {
          return window.location.origin;
        }
      } catch {
        // Fall through to local defaults.
      }
    }
    return window.location.origin;
  }

  if (explicitBase) {
    try {
      return new URL(explicitBase).origin;
    } catch {
      const fallback = toOrigin(explicitBase, "http");
      if (fallback) return fallback;
    }
  }

  // In local UI development, default to local backend unless an explicit base URL is set.
  if (isLocalDevHost) {
    return DEFAULT_LOCAL_API_ORIGIN;
  }

  const host = import.meta.env.VITE_API_URL;
  if (host) {
    const useHttpForLocal =
      /^localhost(?::\d+)?$|^127\.0\.0\.1(?::\d+)?$/i.test(host);
    const normalized = toOrigin(host, useHttpForLocal ? "http" : "https");
    if (normalized) return normalized;
  }

  return DEFAULT_LOCAL_API_ORIGIN;
})();

const API_BASE = (() => {
  const explicitBase = import.meta.env.VITE_API_BASE_URL;

  // Local dev should use Vite proxy instead of absolute backend origin.
  if (isLocalDevHost) return "/api";

  if (explicitBase) return explicitBase.replace(/\/+$/, "");
  return `${BACKEND_ORIGIN}/api`;
})();

const AUTH_RELOAD_TS_KEY = "auth-reload-last-ts";
const AUTH_RELOAD_COOLDOWN_MS = 5 * 60 * 1000;

async function authFetch(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });

  if (response.status === 401) {
    const now = Date.now();
    const lastReloadTs = Number(
      sessionStorage.getItem(AUTH_RELOAD_TS_KEY) || "0",
    );
    const canReload =
      !lastReloadTs || now - lastReloadTs > AUTH_RELOAD_COOLDOWN_MS;

    if (canReload) {
      sessionStorage.setItem(AUTH_RELOAD_TS_KEY, String(now));
      console.warn("Session expired or invalid. Redirecting to login...");
      window.location.reload();
    }
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
  const res = await authFetch(path, {
    // Use authFetch
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

export async function sendTestEmail(payload = null) {
  return requestJson(`${API_BASE}/mail/send-daily-report`, "POST", payload);
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
    const fallbackUrl = `${BACKEND_ORIGIN}/api/scheduler/history`;
    const res = await fetch(fallbackUrl, { credentials: "include" });
    if (!res.ok) {
      throw new Error(`Request failed: ${res.status}`);
    }
    return res.json();
  }
}

export function startScheduler(startTime) {
  return requestJson(`${API_BASE}/scheduler/start`, "POST", {
    start_time: startTime,
  });
}

export function stopSchedulerApi() {
  return requestJson(`${API_BASE}/scheduler/stop`, "POST");
}

export function checkAdminStatus() {
  return request(`${API_BASE}/scheduler/check-admin-status`);
}
