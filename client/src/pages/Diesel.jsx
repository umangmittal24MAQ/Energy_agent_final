import {
  CardSkeleton,
  ChartSkeleton,
  TableSkeleton,
} from "../components/Skeleton";
import { useState, useMemo } from "react";
import { useKpis, useUnifiedData } from "../lib/hooks";
import KpiCard from "../components/KpiCard";
import {
  Fuel,
  Zap,
  TrendingUp,
  AlertCircle,
  Calendar,
  ArrowUpDown,
} from "lucide-react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";

function formatLongDate(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  if (isNaN(d)) return dateStr;
  return d.toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

function formatDateTick(dateStr) {
  return formatLongDate(dateStr);
}

function formatNumber(v) {
  if (v == null) return "—";
  return typeof v === "number"
    ? v.toLocaleString("en-IN", { maximumFractionDigits: 1 })
    : v;
}

function parseNumeric(value) {
  if (value == null || value === "") return 0;
  if (typeof value === "number") return Number.isFinite(value) ? value : 0;
  const text = String(value);
  const direct = Number(text);
  if (Number.isFinite(direct)) return direct;
  const match = text.match(/[-+]?\d*\.?\d+/);
  return match ? Number(match[0]) : 0;
}

function safeNumber(value) {
  return Number.isNaN(value) || value == null ? 0 : value;
}

function getLocalDateKey(dateObj) {
  const year = dateObj.getFullYear();
  const month = String(dateObj.getMonth() + 1).padStart(2, "0");
  const day = String(dateObj.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function normalizeRowDateKey(value) {
  if (!value) return "";
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return value;
  }
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return getLocalDateKey(d);
}

const PAGE_SIZE = 15;

export default function Diesel() {
  const { data: kpis, isLoading: kpisLoading, error: kpisError } = useKpis();
  const {
    data: rawData,
    isLoading: dataLoading,
    error: dataError,
  } = useUnifiedData();

  const dateRange = rawData?.date_range || null;
  const sourceRows = rawData?.data || [];
  const todayDateKey = useMemo(() => getLocalDateKey(new Date()), []);

  const chartData = useMemo(() => {
    const sanitized = sourceRows.map((row) => ({
      date: row["Date"],
      diesel: safeNumber(parseNumeric(row["Diesel consumed"])),
    }));

    if (sanitized.length === 0) {
      const previousDay = new Date(`${todayDateKey}T00:00:00`);
      previousDay.setDate(previousDay.getDate() - 1);
      return [
        { date: getLocalDateKey(previousDay), diesel: 0 },
        { date: todayDateKey, diesel: 0 },
      ];
    }

    return [...sanitized].sort((a, b) => {
      const aKey = normalizeRowDateKey(a.date);
      const bKey = normalizeRowDateKey(b.date);
      return aKey.localeCompare(bKey);
    });
  }, [sourceRows, todayDateKey]);

  const dieselMetrics = useMemo(() => {
    const rowsWithDateKey = sourceRows.map((row) => ({
      row,
      dateKey: normalizeRowDateKey(row["Date"]),
    }));

    const todayRows = rowsWithDateKey
      .filter((item) => item.dateKey === todayDateKey)
      .map((item) => item.row);

    const latestDateKey = rowsWithDateKey
      .map((item) => item.dateKey)
      .filter(Boolean)
      .sort()
      .at(-1);

    const fallbackRows = rowsWithDateKey
      .filter((item) => item.dateKey === latestDateKey)
      .map((item) => item.row);

    const selectedRows = todayRows.length > 0 ? todayRows : fallbackRows;

    const dieselUnitsRaw = selectedRows.reduce(
      (sum, row) => sum + parseNumeric(row["Diesel consumed"]),
      0,
    );
    const gridUnitsRaw = selectedRows.reduce(
      (sum, row) => sum + parseNumeric(row["Grid Units Consumed (KWh)"]),
      0,
    );
    const solarUnitsRaw = selectedRows.reduce(
      (sum, row) => sum + parseNumeric(row["Solar Units Consumed(KWh)"]),
      0,
    );

    const dieselUnits = safeNumber(dieselUnitsRaw);
    const denominator =
      safeNumber(gridUnitsRaw) + safeNumber(solarUnitsRaw) + dieselUnits;
    const dieselContributionPct =
      denominator > 0 ? ((dieselUnits / denominator) * 100).toFixed(2) : "0";

    return {
      dieselConsumedDisplay: Math.ceil(dieselUnits),
      dieselContributionDisplay: `${dieselContributionPct}%`,
    };
  }, [sourceRows, todayDateKey]);

  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState("date");
  const [sortAsc, setSortAsc] = useState(false);

  const allDieselValuesZero = useMemo(
    () => chartData.every((row) => safeNumber(row.diesel) === 0),
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

  const error = kpisError || dataError;

  const TABLE_COLS = [
    { key: "date", label: "Date", format: formatLongDate },
    { key: "diesel", label: "Diesel Consumed", format: formatNumber },
  ];

  return (
    <div className="px-8 py-6 space-y-6 bg-gray-100 rounded-3xl h-[calc(100vh-2rem)] flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-xl font-semibold text-slate-900 flex items-center gap-2">
            <Fuel className="w-5 h-5 text-red-500" />
            Diesel
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Diesel consumption tracking and daily usage breakdown
          </p>
        </div>
        {dateRange && (
          <div className="flex items-center gap-1.5 text-xs text-slate-400">
            <Calendar className="w-3.5 h-3.5" />
            {formatLongDate(dateRange.min_date)} —{" "}
            {formatLongDate(dateRange.max_date)}
          </div>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2.5 text-sm text-red-600 border border-red-200 bg-red-50 px-5 py-3 rounded-lg shrink-0">
          <AlertCircle className="w-4 h-4 shrink-0" />
          Failed to load: {error.message}
        </div>
      )}

      {/* Diesel KPIs */}
      <div className="grid grid-cols-2 gap-4 shrink-0">
        {kpisLoading || dataLoading ? (
          Array.from({ length: 2 }).map((_, i) => <CardSkeleton key={i} />)
        ) : (
          <>
            <KpiCard
              label="Diesel Consumed"
              value={dieselMetrics.dieselConsumedDisplay}
              unit="L"
              icon={Fuel}
              accent="text-red-600"
              iconBg="bg-red-50"
            />
            <KpiCard
              label="Diesel Contribution"
              value={dieselMetrics.dieselContributionDisplay}
              unit=""
              icon={Zap}
              accent="text-blue-600"
              iconBg="bg-blue-50"
            />
          </>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto space-y-6 pr-1">
        {/* Diesel Consumption Trend */}
        {dataLoading ? (
          <ChartSkeleton />
        ) : (
          <section className="bg-white rounded-lg border border-slate-200">
            <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <TrendingUp className="w-4 h-4 text-red-500" />
                <h2 className="text-sm font-medium text-slate-700">
                  Diesel Consumption Trend
                </h2>
              </div>
              {dateRange && (
                <span className="text-xs text-slate-400">
                  as of {formatLongDate(dateRange.max_date)}
                </span>
              )}
            </div>
            <div className="p-5">
              <ResponsiveContainer width="100%" height={320}>
                <AreaChart data={chartData}>
                  <defs>
                    <linearGradient id="dieselGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop
                        offset="5%"
                        stopColor="#ef4444"
                        stopOpacity={0.15}
                      />
                      <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis
                    dataKey="date"
                    tickFormatter={formatDateTick}
                    tick={{ fontSize: 11 }}
                    stroke="#94a3b8"
                    minTickGap={40}
                  />
                  <YAxis
                    tick={{ fontSize: 11 }}
                    stroke="#94a3b8"
                    width={60}
                    allowDataOverflow={false}
                    domain={[0, "auto"]}
                    label={{
                      value: "Diesel Consumed (L)",
                      angle: -90,
                      position: "insideLeft",
                    }}
                  />
                  <Tooltip
                    labelFormatter={formatDateTick}
                    contentStyle={{
                      borderRadius: 8,
                      border: "1px solid #e2e8f0",
                      fontSize: 12,
                      boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.1)",
                    }}
                  />
                  <Legend
                    verticalAlign="top"
                    wrapperStyle={{
                      fontSize: 12,
                      paddingTop: 8,
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="diesel"
                    name="Diesel Consumed"
                    stroke="#ef4444"
                    strokeWidth={2}
                    fill="url(#dieselGrad)"
                  />
                </AreaChart>
              </ResponsiveContainer>
              {allDieselValuesZero && (
                <p className="mt-3 text-xs text-slate-500 text-center">
                  No diesel consumption recorded for this period.
                </p>
              )}
            </div>
          </section>
        )}

        {/* Diesel Data Table */}
        {dataLoading ? (
          <TableSkeleton rows={4} cols={2} />
        ) : (
          <section className="bg-white rounded-lg border border-slate-200">
            <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Fuel className="w-4 h-4 text-red-500" />
                <h2 className="text-sm font-medium text-slate-700">
                  Daily Diesel Data
                </h2>
              </div>
              <span className="bg-white rounded-xl px-2 border border-slate-200 ">
                {chartData.length} records
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50">
                    {TABLE_COLS.map((col) => (
                      <th
                        key={col.key}
                        onClick={() => toggleSort(col.key)}
                        className="px-5 py-3 text-xs font-medium text-slate-500 uppercase tracking-wide cursor-pointer select-none hover:text-slate-700"
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
                        <td key={col.key} className="px-5 py-3 text-slate-700">
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
