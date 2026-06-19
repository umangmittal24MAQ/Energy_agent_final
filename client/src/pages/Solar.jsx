import {
  CardSkeleton,
  ChartSkeleton,
  TableSkeleton,
} from "../components/Skeleton";
import { PieChart, Pie, Cell } from "recharts";
import { useState, useMemo, useEffect, useCallback } from "react";
import { useUnifiedData, useInverterUptime, useWeather } from "../lib/hooks";
import {
  formatLongDate,
  formatNumber,
  getLocalDateKey,
  normalizeRowDateKey,
  sortRowsByDateAsc,
  PAGE_SIZE,
} from "../lib/utils";
import {
  COL,
  CHART_COLORS,
  CHART_AXIS,
  INVERTER_COUNT,
} from "../lib/constants";
import KpiCard from "../components/KpiCard";
import { Sun, PiggyBank, TrendingUp, AlertCircle, Calendar, Zap, ArrowUpDown, Clock } from "lucide-react";
import { WeatherWidget } from "../components/WeatherWidget";
import { TemperatureWidget } from "../components/TemperatureWidget";

import SolarExpectedActual from "../components/SolarExpectedActual";

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
  LineChart,
  Line,
} from "recharts";

// ── helpers ───────────────────────────────────────────────────────────────────
function parseNumeric(value) {
  if (value == null || value === "") return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const text = String(value);
  const direct = Number(text);
  if (Number.isFinite(direct)) return direct;
  const match = text.match(/[-+]?\d*\.?\d+/);
  return match ? Number(match[0]) : null;
}

function todayISO() {
  return new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD, locale-safe
}

// ── Inverter colour palette (matches backend INVERTERS order) ─────────────────
const INV_COLORS = {
  Inverter1: "#f59e0b",
  Inverter2: "#10b981",
  Inverter3: "#3b82f6",
  Inverter4: "#8b5cf6",
  Inverter5: "#ef4444",
};

// ── useInverterUptimeForDate — date-selective fetch ───────────────────────────
// Separate from the existing useInverterUptime hook so we don't break anything
// that depends on it. For today we call the same endpoint with no param (live
// from UnifiedSolarData). For past dates the backend reads inverter_tracker.json.
function useInverterUptimeForDate(dateStr) {
  const [data, setData]       = useState(null);
  const [isLoading, setLoading] = useState(false);
  const [error, setError]     = useState(null);

  const fetch_ = useCallback(async (d) => {
    setLoading(true);
    setError(null);
    try {
      const param = d === todayISO() ? "" : `?date=${d}`;
      const res   = await fetch(`/api/data/live/inverter-uptime${param}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetch_(dateStr); }, [dateStr]);

  return { data, isLoading, error, refetch: () => fetch_(dateStr) };
}

// ── useInverterTrend — 30-day trend from tracker ──────────────────────────────
function useInverterTrend(days = 30) {
  const [data, setData]         = useState(null);
  const [isLoading, setLoading] = useState(false);
  const [error, setError]       = useState(null);

  const fetch_ = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/data/inverter-uptime/trend?days=${days}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => { fetch_(); }, [fetch_]);

  return { data, isLoading, error, refetch: fetch_ };
}

// ── Custom trend tooltip ──────────────────────────────────────────────────────
function TrendTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div
      style={{
        background: "#fff",
        border: "1px solid #e2e8f0",
        borderRadius: 8,
        padding: "10px 14px",
        fontSize: 12,
        boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.08)",
      }}
    >
      <p style={{ color: "#64748b", marginBottom: 6, fontWeight: 500 }}>{label}</p>
      {payload.map((p) => (
        <div key={p.dataKey} style={{ color: p.color, marginBottom: 2 }}>
          {p.dataKey}:{" "}
          <strong>
            {p.value != null ? `${p.value}%` : "—"}
          </strong>
        </div>
      ))}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function Solar({
  embedded = false,
  startDate: propStartDate,
  endDate: propEndDate,
}) {
  const {
    data: unified,
    isLoading: dataLoading,
    error: dataError,
  } = useUnifiedData();

  // Keep the original hook for day_generation_kwh used by SolarExpectedActual
  const { data: uptimeTodayData } = useInverterUptime();

  const { data: weatherData } = useWeather();

  // ── Date-selective inverter uptime state ──────────────────────────────────
  const [uptimeDate, setUptimeDate] = useState(todayISO());
  const isUptimeToday = uptimeDate === todayISO();

  const {
    data: uptimeData,
    isLoading: uptimeLoading,
    error: uptimeError,
    refetch: refetchUptime,
  } = useInverterUptimeForDate(uptimeDate);

  // ── 30-day trend ──────────────────────────────────────────────────────────
  const {
    data: trendData,
    isLoading: trendLoading,
    error: trendError,
    refetch: refetchTrend,
  } = useInverterTrend(30);

  // Flatten trend for Recharts: [{date: "MM-DD", Inverter1: pct, ...}]
  const trendChartData = useMemo(
    () =>
      (trendData?.trend ?? []).map((row) => ({
        date: row.date.slice(5), // "MM-DD"
        fullDate: row.date,
        tracker_found: row.tracker_found,
        ...Object.fromEntries(
          (trendData?.inverters ?? []).map((inv) => [
            inv,
            row.tracker_found ? (row[inv]?.uptime_pct ?? null) : null,
          ])
        ),
      })),
    [trendData]
  );

  // ── Table / chart state ───────────────────────────────────────────────────
  const [page, setPage]       = useState(0);
  const [sortKey, setSortKey] = useState("date");
  const [sortAsc, setSortAsc] = useState(false);

  const dateRange  = unified?.date_range || null;
  const sourceRows = useMemo(() => unified?.data || [], [unified?.data]);
  const hasDateFilter = embedded && propStartDate && propEndDate;

  const todayActualKwh = uptimeTodayData?.day_generation_kwh ?? 0;

  const metricValues = useMemo(() => {
    if (sourceRows.length === 0) return { solarUnits: 0, solarCostSaving: 0, solarShare: "0" };

    let selectedRows;
    if (hasDateFilter) {
      selectedRows = sourceRows.filter((row) => {
        const key = normalizeRowDateKey(row[COL.DATE]);
        return key && key >= propStartDate && key <= propEndDate;
      });
    } else {
      const yesterday = new Date();
      yesterday.setDate(yesterday.getDate() - 1);
      const todayKey = getLocalDateKey(yesterday);
      const rowsWithKey = sourceRows.map((row) => ({
        row,
        dateKey: normalizeRowDateKey(row[COL.DATE]),
      }));
      const todayRows = rowsWithKey.filter((i) => i.dateKey === todayKey).map((i) => i.row);
      const latestDateKey = rowsWithKey.map((i) => i.dateKey).filter(Boolean).sort().at(-1);
      const fallbackRows = rowsWithKey.filter((i) => i.dateKey === latestDateKey).map((i) => i.row);
      selectedRows = todayRows.length > 0 ? todayRows : fallbackRows;
    }

    const solarUnitsRaw     = selectedRows.reduce((s, r) => s + (parseNumeric(r[COL.SOLAR_UNITS])  || 0), 0);
    const gridUnitsRaw      = selectedRows.reduce((s, r) => s + (parseNumeric(r[COL.GRID_UNITS])   || 0), 0);
    const dieselUnitsRaw    = selectedRows.reduce((s, r) => s + (parseNumeric(r[COL.DIESEL])       || 0), 0);
    const solarCostSavingRaw = selectedRows.reduce((s, r) => s + (parseNumeric(r[COL.ENERGY_SAVINGS]) || 0), 0);
    const denominator       = gridUnitsRaw + solarUnitsRaw + dieselUnitsRaw;

    return {
      solarUnits:      Math.ceil(solarUnitsRaw),
      solarCostSaving: Math.ceil(solarCostSavingRaw),
      solarShare:      denominator > 0 ? ((solarUnitsRaw / denominator) * 100).toFixed(2) : "0",
    };
  }, [sourceRows, hasDateFilter, propStartDate, propEndDate]);

  const allChartData = useMemo(
    () =>
      (unified?.data || []).map((row) => ({
        date:    row[COL.DATE],
        solar:   row[COL.SOLAR_UNITS] ?? row[COL.DAY_GENERATION] ?? 0,
        total:   row[COL.TOTAL_UNITS] ?? 0,
        savings: row[COL.ENERGY_SAVINGS] ?? 0,
      })),
    [unified]
  );

  const chartData = useMemo(() => {
    if (!hasDateFilter) return allChartData;
    return allChartData.filter((row) => {
      const key = normalizeRowDateKey(row.date);
      return key && key >= propStartDate && key <= propEndDate;
    });
  }, [allChartData, hasDateFilter, propStartDate, propEndDate]);

  const trendChartDataSolar = useMemo(() => sortRowsByDateAsc(chartData), [chartData]);

  const sorted = useMemo(() => {
    const copy = [...chartData];
    copy.sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      if (av < bv) return sortAsc ? -1 : 1;
      if (av > bv) return sortAsc ? 1 : -1;
      return 0;
    });
    return copy;
  }, [chartData, sortKey, sortAsc]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const pageData  = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  function toggleSort(key) {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(true); }
    setPage(0);
  }

  const TABLE_COLS = [
    { key: "date",    label: "Date",                        format: formatLongDate },
    { key: "solar",   label: "Solar Units Consumed (KWh)",  format: formatNumber },
    { key: "savings", label: "Solar Cost Saving (INR)",     format: formatNumber },
  ];

  const rootClass    = embedded ? "space-y-6" : "px-8 py-6 space-y-6 bg-gray-100 rounded-3xl";
  const contentClass = embedded ? "space-y-6" : "space-y-6";

  return (
    <div className={rootClass}>
      {/* ── Page header ── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900 flex items-center gap-2">
            <Sun className="w-5 h-5 text-amber-500" />
            Solar
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Solar generation performance, savings, and detailed daily breakdown
          </p>
        </div>
        {!embedded && dateRange && (
          <div className="flex items-center gap-1.5 text-xs text-slate-400">
            <Calendar className="w-3.5 h-3.5" />
            {formatLongDate(dateRange.min_date)} — {formatLongDate(dateRange.max_date)}
          </div>
        )}
      </div>

      {dataError && (
        <div className="flex items-center gap-2.5 text-sm text-red-600 border border-red-200 bg-red-50 px-5 py-3 rounded-lg">
          <AlertCircle className="w-4 h-4 shrink-0" />
          Failed to load: {dataError.message}
        </div>
      )}

      <div className={contentClass}>
        {/* ── Live Weather + Solar Impact ── */}
        <WeatherWidget />

        <TemperatureWidget />

        {/* ── Expected vs Actual ── */}
        <SolarExpectedActual actualKwh={todayActualKwh} weather={weatherData} />

        {/* ── Solar Generation Trend chart ── */}
        {dataLoading ? (
          <ChartSkeleton />
        ) : (
          <section className="bg-white rounded-lg border border-slate-200 animate-scale-in">
            <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <TrendingUp className="w-4 h-4 text-amber-600" />
                <h2 className="text-sm font-medium text-slate-700">Solar Generation Trend</h2>
              </div>
              {dateRange && (
                <span className="text-xs text-slate-400">
                  as of {formatLongDate(dateRange.max_date)}
                </span>
              )}
            </div>
            <div className="pr-5 pt-5">
              <ResponsiveContainer width="100%" height={360}>
                <AreaChart data={trendChartDataSolar} margin={{ top: 8, right: 8, left: 0, bottom: 40 }}>
                  <defs>
                    <linearGradient id="solarGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor={CHART_COLORS.solar} stopOpacity={0.2} />
                      <stop offset="95%" stopColor={CHART_COLORS.solar} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke={CHART_AXIS.gridStroke} />
                  <XAxis dataKey="date" tickFormatter={formatLongDate} tick={{ fontSize: 11 }} stroke={CHART_AXIS.tickStroke} minTickGap={40} />
                  <YAxis tick={{ fontSize: 11 }} stroke={CHART_AXIS.tickStroke} width={60} />
                  <Tooltip labelFormatter={formatLongDate} contentStyle={{ borderRadius: 8, border: CHART_AXIS.tooltipBorder, fontSize: 12, boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.1)" }} />
                  <Legend verticalAlign="bottom" height={36} />
                  <Area type="monotone" dataKey="solar" name="Solar Units Consumed (KWh)" stroke={CHART_COLORS.solar} strokeWidth={2} fill="url(#solarGrad)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </section>
        )}

        {/* ── Inverter Uptime (date-selective) ── */}
        <section className="bg-white rounded-lg border border-slate-200 animate-slide-up">
          {/* Section header with date picker */}
          <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between flex-wrap gap-3">
            <div className="flex items-center gap-2">
              <Clock className="w-4 h-4 text-amber-600" />
              <h2 className="text-sm font-medium text-slate-700">
                Inverter Uptime
              </h2>
              {!isUptimeToday && (
                <span className="text-xs text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
                  historical · tracker
                </span>
              )}
            </div>

            <div className="flex items-center gap-2">
              {/* as-of timestamp */}
              {uptimeData?.as_of && (
                <span className="text-xs text-slate-400 hidden sm:block">
                  {isUptimeToday
                    ? `as of ${new Date(uptimeData.as_of).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })} · ${uptimeData.rows_processed} readings`
                    : `${uptimeData.rows_processed} readings`}
                </span>
              )}

              {/* Date picker */}
              <input
                type="date"
                value={uptimeDate}
                max={todayISO()}
                onChange={(e) => e.target.value && setUptimeDate(e.target.value)}
                className="text-xs border border-slate-200 rounded-md px-2 py-1.5 text-slate-600 bg-white focus:outline-none focus:ring-1 focus:ring-amber-400 cursor-pointer"
              />

              {/* Jump-to-today button (only shows when a past date is selected) */}
              {!isUptimeToday && (
                <button
                  onClick={() => setUptimeDate(todayISO())}
                  className="text-xs text-amber-600 border border-amber-200 bg-amber-50 hover:bg-amber-100 rounded-md px-2.5 py-1.5 transition-colors"
                >
                  Today
                </button>
              )}

              {/* Refresh */}
              <button
                onClick={refetchUptime}
                className="text-xs text-slate-400 border border-slate-200 rounded-md px-2.5 py-1.5 hover:bg-slate-50 transition-colors"
                title="Refresh"
              >
                ↻
              </button>
            </div>
          </div>

          {/* Per-inverter cards */}
          {uptimeLoading && (
            <div className="p-5 text-sm text-slate-400">Loading uptime data…</div>
          )}
          {uptimeError && (
            <div className="p-5 flex items-center gap-2 text-sm text-red-600">
              <AlertCircle className="w-4 h-4 shrink-0" />
              Failed to load uptime data
            </div>
          )}
          {uptimeData && !uptimeLoading && (
            <>
              {/* No-data banner for past dates where tracker has nothing */}
              {uptimeData.tracker_found === false && !isUptimeToday && (
                <div className="mx-5 mt-4 flex items-center gap-2 text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded-md px-4 py-2.5">
                  <AlertCircle className="w-3.5 h-3.5 shrink-0 text-slate-400" />
                  No tracker data available for {uptimeDate}. Tracker retains up to 30 days.
                </div>
              )}

              <div className="p-5 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
                {Object.entries(uptimeData.inverters).map(([inv, stats]) => {
                  const hasFault = stats.downtime_mins > 0;
                  const color    = INV_COLORS[inv] ?? "#64748b";
                  return (
                    <div key={inv} className="rounded-lg border border-slate-200 p-3 bg-slate-50/60">
                      <div className="flex items-center justify-between">
                        <p className="text-xs font-semibold text-slate-500">{inv}</p>
                        <span
                          className="w-2 h-2 rounded-full"
                          style={{ background: color }}
                        />
                      </div>

                      {/* Uptime bar */}
                      <div className="mt-2 h-1.5 w-full rounded-full bg-slate-200 overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{
                            width: `${stats.uptime_pct}%`,
                            background: hasFault ? "#ef4444" : "#10b981",
                          }}
                        />
                      </div>

                      <p
                        className="mt-1.5 text-lg font-bold"
                        style={{ color: hasFault ? "#dc2626" : "#059669" }}
                      >
                        {stats.uptime_pct}%
                      </p>

                      <div className="mt-1 space-y-0.5 text-[11px] text-slate-500">
                        <p>↑ Up: {stats.uptime_hrs}h ({stats.uptime_mins}m)</p>
                        <p>↓ Down: {stats.downtime_hrs}h ({stats.downtime_mins}m)</p>
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </section>

        {/*{/* ── 30-day Inverter Downtime Breakdown (Pie) ── */}
{!trendLoading && !trendError && trendChartData.length > 0 && (() => {
  const inverters = trendData?.inverters ?? [];

  const downtimeTotals = Object.fromEntries(inverters.map((inv) => [inv, 0]));
  (trendData?.trend ?? []).forEach((row) => {
    if (!row.tracker_found) return;
    inverters.forEach((inv) => {
      downtimeTotals[inv] += row[inv]?.downtime_mins ?? 0;
    });
  });

  const totalMins = inverters.reduce((s, inv) => s + downtimeTotals[inv], 0);
  const sorted = [...inverters].sort((a, b) => downtimeTotals[b] - downtimeTotals[a]);

  const PIE_DATA = {
    labels: sorted,
    datasets: [{
      data: sorted.map((inv) => downtimeTotals[inv]),
      backgroundColor: sorted.map((inv) => INV_COLORS[inv] ?? "#94a3b8"),
      borderWidth: 2,
      borderColor: "#ffffff",
      hoverOffset: 6,
    }],
  };

  return (
    <section className="bg-white rounded-lg border border-slate-200 animate-slide-up">
      <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <TrendingUp className="w-4 h-4 text-amber-600" />
          <h2 className="text-sm font-medium text-slate-700">
            30-Day Inverter Downtime Breakdown
          </h2>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400 hidden sm:block">
            cumulative fault minutes · from tracker
          </span>
          <button
            onClick={refetchTrend}
            className="text-xs text-slate-400 border border-slate-200 rounded-md px-2.5 py-1.5 hover:bg-slate-50 transition-colors"
            title="Refresh"
          >
            ↻
          </button>
        </div>
      </div>

      <div className="p-5 flex flex-col sm:flex-row items-center gap-8">
        {/* Pie via Recharts PieChart */}
        <div className="w-56 h-56 shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={sorted.map((inv) => ({
                  name: inv,
                  value: downtimeTotals[inv] || 0.01, // avoid zero-slice rendering
                }))}
                cx="50%"
                cy="50%"
                innerRadius="55%"
                outerRadius="80%"
                paddingAngle={2}
                dataKey="value"
              >
                {sorted.map((inv) => (
                  <Cell key={inv} fill={INV_COLORS[inv] ?? "#94a3b8"} />
                ))}
              </Pie>
              <Tooltip
                formatter={(value, name) => {
                  const mins = downtimeTotals[name];
                  const pct = totalMins > 0 ? ((mins / totalMins) * 100).toFixed(1) : "0";
                  const h = Math.floor(mins / 60), m = mins % 60;
                  return [`${pct}% · ${h > 0 ? h + "h " : ""}${m}m`, name];
                }}
                contentStyle={{ borderRadius: 8, fontSize: 12, border: "1px solid #e2e8f0" }}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* Legend */}
        <div className="flex flex-col gap-3 flex-1 min-w-0">
          {sorted.map((inv) => {
            const mins = downtimeTotals[inv];
            const pct = totalMins > 0 ? ((mins / totalMins) * 100).toFixed(1) : "0.0";
            const h = Math.floor(mins / 60), m = mins % 60;
            const timeStr = h > 0 ? `${h}h ${m}m` : `${m}m`;
            return (
              <div key={inv} className="flex items-center gap-2.5 text-sm">
                <span
                  className="w-2.5 h-2.5 rounded-sm shrink-0"
                  style={{ background: INV_COLORS[inv] ?? "#94a3b8" }}
                />
                <span className="flex-1 text-slate-700">{inv}</span>
                <span className="font-medium text-slate-800">{pct}%</span>
                <span className="text-xs text-slate-400 w-16 text-right">{timeStr}</span>
              </div>
            );
          })}
          <p className="text-[11px] text-slate-400 mt-1 pt-2 border-t border-slate-100">
            Total: {Math.floor(totalMins / 60)}h {totalMins % 60}m across 30 days · gaps excluded
          </p>
        </div>
      </div>
    </section>
  );
})()}

{trendLoading && <div className="p-5 text-sm text-slate-400">Loading trend…</div>}
{trendError && (
  <div className="p-5 flex items-center gap-2 text-sm text-red-600">
    <AlertCircle className="w-4 h-4 shrink-0" />
    Failed to load trend data
  </div>
)}

        {/* ── Daily Solar Data table ── */}
        {dataLoading ? (
          <TableSkeleton rows={4} cols={4} />
        ) : (
          <section className="bg-white rounded-lg border border-slate-200 animate-slide-up">
            <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Sun className="w-4 h-4 text-amber-600" />
                <h2 className="text-sm font-medium text-slate-700">Daily Solar Data</h2>
              </div>
              <span className="text-xs text-slate-400">{sorted.length} records</span>
            </div>
            <div className="max-h-[70vh] overflow-auto">
              <table className="energy-table w-full text-sm text-left">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50">
                    {TABLE_COLS.map((col) => (
                      <th
                        key={col.key}
                        onClick={() => toggleSort(col.key)}
                        className="px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wide cursor-pointer select-none hover:text-slate-700 whitespace-nowrap sticky top-0 z-10 bg-slate-50"
                      >
                        <span className="inline-flex items-center gap-1">
                          {col.label}
                          <ArrowUpDown className={`w-3 h-3 ${sortKey === col.key ? "text-blue-600" : "text-slate-300"}`} />
                        </span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {pageData.map((row, i) => (
                    <tr
                      key={row.date + i}
                      className="border-b border-slate-100 last:border-0 hover:bg-slate-50 transition-colors"
                    >
                      {TABLE_COLS.map((col) => (
                        <td key={col.key} className="px-4 py-3 text-slate-700">
                          {col.format(row[col.key])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {pageCount > 1 && (
              <div className="px-5 py-3 border-t border-slate-200 flex items-center justify-between text-xs text-slate-500">
                <span>Page {page + 1} of {pageCount}</span>
                <div className="flex gap-1">
                  <button
                    onClick={() => setPage(Math.max(0, page - 1))}
                    disabled={page === 0}
                    className="px-3 py-1.5 border border-slate-200 rounded-md enabled:hover:bg-slate-50 disabled:opacity-40 cursor-pointer disabled:cursor-default transition-colors"
                  >
                    Prev
                  </button>
                  <button
                    onClick={() => setPage(Math.min(pageCount - 1, page + 1))}
                    disabled={page >= pageCount - 1}
                    className="px-3 py-1.5 border border-slate-200 rounded-md enabled:hover:bg-slate-50 disabled:opacity-40 cursor-pointer disabled:cursor-default transition-colors"
                  >
                    Next
                  </button>
                </div>
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}