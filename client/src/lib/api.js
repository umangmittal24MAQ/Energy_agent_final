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
    const res = await fetch(`${API_BASE}/mail/send-daily-report`, {
        method: "POST",
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Request failed: ${res.status}`);
    }
    return res.json();
}
