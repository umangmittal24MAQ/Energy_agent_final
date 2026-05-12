import {
  CardSkeleton,
  ChartSkeleton,
  TableSkeleton,
} from "../components/Skeleton";
import { useState, useMemo } from "react";
import { useUnifiedData, useInverterUptime } from "../lib/hooks";
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

// Solar-specific: returns null for missing values (used by inverter display)
function parseNumeric(value) {
  if (value == null || value === "") return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const text = String(value);
  const direct = Number(text);
  if (Number.isFinite(direct)) return direct;
  const match = text.match(/[-+]?\d*\.?\d+/);
  return match ? Number(match[0]) : null;
}



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

  const {
  data: uptimeData,
  isLoading: uptimeLoading,
  error: uptimeError,
} = useInverterUptime();

  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState("date");
  const [sortAsc, setSortAsc] = useState(false);

  const dateRange = unified?.date_range || null;
  const sourceRows = useMemo(() => unified?.data || [], [unified?.data]);

  // Date range: use props when embedded, otherwise full range
  const hasDateFilter = embedded && propStartDate && propEndDate;

  const metricValues = useMemo(() => {
    if (sourceRows.length === 0) {
      return {
        solarUnits: 0,
        solarCostSaving: 0,
        solarShare: "0",
      };
    }

    let selectedRows;
    if (hasDateFilter) {
      selectedRows = sourceRows.filter((row) => {
        const key = normalizeRowDateKey(row[COL.DATE]);
        if (!key) return false;
        return key >= propStartDate && key <= propEndDate;
      });
    } else {
      const yesterday = new Date();
      yesterday.setDate(yesterday.getDate() - 1);
      const todayKey = getLocalDateKey(yesterday);  
      const rowsWithKey = sourceRows.map((row) => ({
        row,
        dateKey: normalizeRowDateKey(row[COL.DATE]),
      }));

      const todayRows = rowsWithKey
        .filter((item) => item.dateKey === todayKey)
        .map((item) => item.row);

      const latestDateKey = rowsWithKey
        .map((item) => item.dateKey)
        .filter(Boolean)
        .sort()
        .at(-1);

      

      const fallbackRows = rowsWithKey
        .filter((item) => item.dateKey === latestDateKey)
        .map((item) => item.row);

      selectedRows = todayRows.length > 0 ? todayRows : fallbackRows;
    }

    const solarUnitsRaw = selectedRows.reduce(
      (sum, row) => sum + (parseNumeric(row[COL.SOLAR_UNITS]) || 0),
      0,
    );
    const gridUnitsRaw = selectedRows.reduce(
      (sum, row) => sum + (parseNumeric(row[COL.GRID_UNITS]) || 0),
      0,
    );
    const dieselUnitsRaw = selectedRows.reduce(
      (sum, row) => sum + (parseNumeric(row[COL.DIESEL]) || 0),
      0,
    );
    const solarCostSavingRaw = selectedRows.reduce(
      (sum, row) => sum + (parseNumeric(row[COL.ENERGY_SAVINGS]) || 0),
      0,
    );

    const denominator = gridUnitsRaw + solarUnitsRaw + dieselUnitsRaw;
    const solarShareValue =
      denominator > 0 ? ((solarUnitsRaw / denominator) * 100).toFixed(2) : "0";

    return {
      solarUnits: Math.ceil(solarUnitsRaw),
      solarCostSaving: Math.ceil(solarCostSavingRaw),
      solarShare: solarShareValue,
    };
  }, [sourceRows, hasDateFilter, propStartDate, propEndDate]);

  const allChartData = useMemo(
    () =>
      (unified?.data || []).map((row) => ({
        date: row[COL.DATE],
        solar: row[COL.SOLAR_UNITS] ?? row[COL.DAY_GENERATION] ?? 0,
        total: row[COL.TOTAL_UNITS] ?? 0,
        savings: row[COL.ENERGY_SAVINGS] ?? 0,
      })),
    [unified],
  );

  const chartData = useMemo(() => {
    if (!hasDateFilter) return allChartData;
    return allChartData.filter((row) => {
      const key = normalizeRowDateKey(row.date);
      if (!key) return false;
      return key >= propStartDate && key <= propEndDate;
    });
  }, [allChartData, hasDateFilter, propStartDate, propEndDate]);

  const trendChartData = useMemo(
    () => sortRowsByDateAsc(chartData),
    [chartData],
  );


  const sorted = useMemo(() => {
    const copy = [...chartData];
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av < bv) return sortAsc ? -1 : 1;
      if (av > bv) return sortAsc ? 1 : -1;
      return 0;
    });
    return copy;
  }, [chartData, sortKey, sortAsc]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const pageData = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  function toggleSort(key) {
    if (sortKey === key) {
      setSortAsc(!sortAsc);
    } else {
      setSortKey(key);
      setSortAsc(true);
    }
    setPage(0);
  }

  const error = dataError;

  const TABLE_COLS = [
  { key: "date", label: "Date", format: formatLongDate },
  { key: "solar", label: "Solar Units Consumed (KWh)", format: formatNumber },
  { key: "savings", label: "Solar Cost Saving (INR)", format: formatNumber },
];

  const rootClass = embedded
    ? "space-y-6"
    : "px-8 py-6 space-y-6 bg-gray-100 rounded-3xl";

  const contentClass = embedded ? "space-y-6" : "space-y-6";

  return (
    <div className={rootClass}>
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
            {formatLongDate(dateRange.min_date)} —{" "}
            {formatLongDate(dateRange.max_date)}
          </div>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2.5 text-sm text-red-600 border border-red-200 bg-red-50 px-5 py-3 rounded-lg">
          <AlertCircle className="w-4 h-4 shrink-0" />
          Failed to load: {error.message}
        </div>
      )}

      <div className={contentClass}>
        {dataLoading ? (
          <ChartSkeleton />
        ) : (
          <section className="bg-white rounded-lg border border-slate-200 animate-scale-in">
            <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <TrendingUp className="w-4 h-4 text-amber-600" />
                <h2 className="text-sm font-medium text-slate-700">
                  Solar Generation Trend
                </h2>
              </div>
              {dateRange && (
                <span className="text-xs text-slate-400">
                  as of {formatLongDate(dateRange.max_date)}
                </span>
              )}
            </div>
            <div className="pr-5 pt-5">
              <ResponsiveContainer width="100%" height={360}>
                <AreaChart
                  data={trendChartData}
                  margin={{
                    top: 8,
                    right: 8,
                    left: 0,
                    bottom: 40,
                  }}
                >
                  <defs>
                    <linearGradient id="solarGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop
                        offset="5%"
                        stopColor={CHART_COLORS.solar}
                        stopOpacity={0.2}
                      />
                      <stop
                        offset="95%"
                        stopColor={CHART_COLORS.solar}
                        stopOpacity={0}
                      />
                    </linearGradient>
                  </defs>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke={CHART_AXIS.gridStroke}
                  />
                  <XAxis
                    dataKey="date"
                    tickFormatter={formatLongDate}
                    tick={{ fontSize: 11 }}
                    stroke={CHART_AXIS.tickStroke}
                    minTickGap={40}
                  />
                  <YAxis
                    tick={{ fontSize: 11 }}
                    stroke={CHART_AXIS.tickStroke}
                    width={60}
                  />
                  <Tooltip
                    labelFormatter={formatLongDate}
                    contentStyle={{
                      borderRadius: 8,
                      border: CHART_AXIS.tooltipBorder,
                      fontSize: 12,
                      boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.1)",
                    }}
                  />
                  {/* ADDED: Legend positioning fix to avoid chart overlap. */}
                  <Legend verticalAlign="bottom" height={36} />
                  <Area
                    type="monotone"
                    dataKey="solar"
                    name="Solar Units Consumed (KWh)"
                    stroke={CHART_COLORS.solar}
                    strokeWidth={2}
                    fill="url(#solarGrad)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </section>
        )}

        {/* ── Today's Inverter Uptime / Downtime ── */}
<section className="bg-white rounded-lg border border-slate-200 animate-slide-up">
  <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
    <div className="flex items-center gap-2">
      <Clock className="w-4 h-4 text-amber-600" />
      <h2 className="text-sm font-medium text-slate-700">
        Today's Inverter Uptime
      </h2>
    </div>
    {uptimeData?.as_of && (
      <span className="text-xs text-slate-400">
        as of {new Date(uptimeData.as_of).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}
        {" · "}{uptimeData.rows_processed} readings
      </span>
    )}
  </div>

  {uptimeLoading && (
    <div className="p-5 text-sm text-slate-400">Loading uptime data...</div>
  )}

  {uptimeError && (
    <div className="p-5 flex items-center gap-2 text-sm text-red-600">
      <AlertCircle className="w-4 h-4 shrink-0" />
      Failed to load uptime data
    </div>
  )}

  {uptimeData && !uptimeLoading && (
    <div className="p-5 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
      {Object.entries(uptimeData.inverters).map(([inv, stats]) => {
        const hasFault = stats.downtime_mins > 0;
        return (
          <div key={inv} className="rounded-lg border border-slate-200 p-3 bg-slate-50/60">
            <p className="text-xs font-semibold text-slate-500">{inv}</p>

            {/* Uptime bar */}
            <div className="mt-2 h-1.5 w-full rounded-full bg-slate-200 overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${hasFault ? "bg-red-400" : "bg-emerald-400"}`}
                style={{ width: `${stats.uptime_pct}%` }}
              />
            </div>

            <p className={`mt-1.5 text-lg font-bold ${hasFault ? "text-red-600" : "text-emerald-600"}`}>
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
  )}
</section>

        {dataLoading ? (
          <TableSkeleton rows={4} cols={4} />
        ) : (
          <section className="bg-white rounded-lg border border-slate-200 animate-slide-up">
            <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Sun className="w-4 h-4 text-amber-600" />
                <h2 className="text-sm font-medium text-slate-700">
                  Daily Solar Data
                </h2>
              </div>
              <span className="text-xs text-slate-400">
                {sorted.length} records
              </span>
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
                          <ArrowUpDown
                            className={`w-3 h-3 ${sortKey === col.key ? "text-blue-600" : "text-slate-300"}`}
                          />
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
                <span>
                  Page {page + 1} of {pageCount}
                </span>
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
