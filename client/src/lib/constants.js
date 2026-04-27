// ── SharePoint / API column name keys ──────────────────────────────────
export const COL = Object.freeze({
    DATE: "Date",
    TIME: "Time",
    AMBIENT_TEMP: "Ambient Temperature °C",
    GRID_UNITS: "Grid Units Consumed (KWh)",
    SOLAR_UNITS: "Solar Units Consumed(KWh)",
    TOTAL_UNITS: "Total Units Consumed (KWh)",
    TOTAL_COST: "Total Units Consumed in INR",
    ENERGY_SAVINGS: "Energy Saving in INR",
    DIESEL: "Diesel consumed",
    PANELS_CLEANED: "Number of Panels Cleaned",
    WATER_STP: "Water treated through STP",
    WATER_WTP: "Water treated through WTP",
    ISSUES: "Issues",
    DAY_GENERATION: "Day Generation (kWh)",
});

// ── Chart series colors ────────────────────────────────────────────────
export const CHART_COLORS = Object.freeze({
    grid: "#475569",
    solar: "#f59e0b",
    total: "#2563eb",
    cost: "#64748b",
    savings: "#10b981",
    diesel: "#ef4444",
});

// ── Chart axis / tooltip styling (shared across all chart pages) ──────
export const CHART_AXIS = Object.freeze({
    gridStroke: "#e2e8f0",
    tickStroke: "#94a3b8",
    tooltipBorder: "1px solid #e2e8f0",
});

// ── Locales ────────────────────────────────────────────────────────────
export const DATE_LOCALE = "en-US";
export const NUMBER_LOCALE = "en-IN";

// ── Branding ───────────────────────────────────────────────────────────
export const APP_NAME = "Energy Dashboard";

// ── Solar ──────────────────────────────────────────────────────────────
export const INVERTER_COUNT = 5;

// ── Auth ───────────────────────────────────────────────────────────────
export const AUTH_SCOPES = ["User.Read"];

// ── Scheduler ──────────────────────────────────────────────────────────
export const SCHEDULER = Object.freeze({
    POLL_INTERVAL_MS: 30_000,
    RETRY_DELAY_MS: 3_000,
    MAX_RETRY_ATTEMPTS: 20,
    TOAST_DURATION_MS: 5_000,
    DISPLAY_HISTORY_COUNT: 5,
    DEFAULT_INTERVAL_MINUTES: 30,
    SLOTS_PER_DAY: 4,
});

// ── React Query ────────────────────────────────────────────────────────
export const QUERY_STALE_TIME_MS = 5 * 60 * 1000;
