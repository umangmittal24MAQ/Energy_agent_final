import { DATE_LOCALE, NUMBER_LOCALE } from "./constants";

export function formatLongDate(dateStr) {
    if (!dateStr) return "";
    const d = new Date(dateStr);
    if (isNaN(d)) return dateStr;
    return d.toLocaleDateString(DATE_LOCALE, {
        year: "numeric",
        month: "long",
        day: "numeric",
    });
}

export function formatNumber(v) {
    if (v == null || v === "") return "—";
    const numeric = typeof v === "number" ? v : Number(v);
    if (Number.isFinite(numeric)) {
        return Math.ceil(numeric).toLocaleString(NUMBER_LOCALE, {
            maximumFractionDigits: 0,
        });
    }
    return v;
}

export function parseNumeric(value) {
    if (value == null || value === "") return 0;
    if (typeof value === "number") return Number.isFinite(value) ? value : 0;
    const text = String(value);
    const direct = Number(text);
    if (Number.isFinite(direct)) return direct;
    const match = text.match(/[-+]?\d*\.?\d+/);
    return match ? Number(match[0]) : 0;
}

export function getLocalDateKey(dateObj) {
    const year = dateObj.getFullYear();
    const month = String(dateObj.getMonth() + 1).padStart(2, "0");
    const day = String(dateObj.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
}

export function normalizeRowDateKey(value) {
    if (!value) return "";
    if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
        return value;
    }
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return "";
    return getLocalDateKey(d);
}

export function sortRowsByDateAsc(rows) {
    return [...rows].sort((a, b) => {
        const aKey = normalizeRowDateKey(a.date) || String(a.date ?? "");
        const bKey = normalizeRowDateKey(b.date) || String(b.date ?? "");
        if (aKey < bKey) return -1;
        if (aKey > bKey) return 1;
        return 0;
    });
}

export const PAGE_SIZE = 15;
