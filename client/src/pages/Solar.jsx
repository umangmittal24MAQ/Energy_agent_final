import {
  CardSkeleton,
  ChartSkeleton,
  TableSkeleton,
} from "../components/Skeleton";
import { useState, useMemo } from "react";
import { useKpis, useUnifiedData } from "../lib/hooks";
import KpiCard from "../components/KpiCard";
import {
  Sun,
  PiggyBank,
  TrendingUp,
  Loader2,
  AlertCircle,
  Calendar,
  Zap,
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

function formatDateTick(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  if (isNaN(d)) return dateStr;
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

function formatNumber(v) {
  if (v == null) return "—";
  return typeof v === "number"
    ? v.toLocaleString("en-IN", { maximumFractionDigits: 1 })
    : v;
}

const PAGE_SIZE = 15;

export default function Solar() {
  const { data: kpis, isLoading: kpisLoading, error: kpisError } = useKpis();
  const {
    data: unified,
    isLoading: dataLoading,
    error: dataError,
  } = useUnifiedData();

  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState("date");
  const [sortAsc, setSortAsc] = useState(false);

  const dateRange = unified?.date_range || null;
  const chartData = useMemo(
    () =>
      (unified?.data || []).map((row) => ({
        date: row["Date"],
        solar: row["Solar Units Consumed(KWh)"] ?? 0,
        total: row["Total Units Consumed (KWh)"] ?? 0,
        savings: row["Energy Saving in INR"] ?? 0,
      })),
    [unified],
  );

  const solarShare =
    kpis?.total_solar_kwh && kpis?.total_energy_kwh
      ? ((kpis.total_solar_kwh / kpis.total_energy_kwh) * 100).toFixed(1)
      : null;

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
    { key: "date", label: "Date", format: (v) => v },
    {
      key: "solar",
      label: "Solar Units Consumed (KWh)",
      format: formatNumber,
    },
    {
      key: "total",
      label: "Total Units Consumed (KWh)",
      format: formatNumber,
    },
    { key: "savings", label: "Energy Saving (INR)", format: formatNumber },
  ];

  return (
    <div className="px-8 py-6 space-y-6 bg-gray-100 rounded-3xl h-[calc(100vh-2rem)] flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-xl font-semibold text-slate-900 flex items-center gap-2">
            <Sun className="w-5 h-5 text-amber-500" />
            Solar
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Solar generation performance, savings, and detailed daily breakdown
          </p>
        </div>
        {dateRange && (
          <div className="flex items-center gap-1.5 text-xs text-slate-400">
            <Calendar className="w-3.5 h-3.5" />
            {dateRange.min_date} — {dateRange.max_date}
          </div>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2.5 text-sm text-red-600 border border-red-200 bg-red-50 px-5 py-3 rounded-lg shrink-0">
          <AlertCircle className="w-4 h-4 shrink-0" />
          Failed to load: {error.message}
        </div>
      )}

      {/* Solar KPIs */}
      <div className="grid grid-cols-3 gap-4 shrink-0">
        {kpisLoading ? (
          Array.from({ length: 3 }).map((_, i) => <CardSkeleton key={i} />)
        ) : (
          <>
            <KpiCard
              label="Solar Units Consumed"
              value={kpis?.total_solar_kwh}
              unit="KWh"
              icon={Sun}
              accent="text-amber-600"
              iconBg="bg-amber-50"
            />
            <KpiCard
              label="Energy Saving"
              value={kpis?.solar_savings_inr}
              unit="INR"
              icon={PiggyBank}
              accent="text-emerald-600"
              iconBg="bg-emerald-50"
            />
            <KpiCard
              label="Solar Share"
              value={solarShare}
              unit="%"
              icon={Zap}
              accent="text-blue-600"
              iconBg="bg-blue-50"
            />
          </>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto space-y-6 pr-1">
        {/* Solar Energy Trend */}
        {dataLoading ? (
          <ChartSkeleton />
        ) : (
          <section className="bg-white rounded-lg border border-slate-200">
            <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <TrendingUp className="w-4 h-4 text-amber-600" />
                <h2 className="text-sm font-medium text-slate-700">
                  Solar Generation Trend
                </h2>
              </div>
              {dateRange && (
                <span className="text-xs text-slate-400">
                  as of {dateRange.max_date}
                </span>
              )}
            </div>
            <div className="p-5">
              <ResponsiveContainer width="100%" height={320}>
                <AreaChart data={chartData}>
                  <defs>
                    <linearGradient id="solarGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.2} />
                      <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
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
                  <YAxis tick={{ fontSize: 11 }} stroke="#94a3b8" width={60} />
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
                    wrapperStyle={{
                      fontSize: 12,
                      paddingTop: 8,
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="solar"
                    name="Solar Units Consumed (KWh)"
                    stroke="#f59e0b"
                    strokeWidth={2}
                    fill="url(#solarGrad)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </section>
        )}

        {/* Solar Data Table */}
        {dataLoading ? (
          <TableSkeleton rows={4} cols={4} />
        ) : (
          <section className="bg-white rounded-lg border border-slate-200">
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

            {/* Pagination */}
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
