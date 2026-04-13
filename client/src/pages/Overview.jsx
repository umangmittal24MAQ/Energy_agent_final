import { useState, useMemo } from "react";
import { useKpis, useUnifiedData } from "../lib/hooks";
import KpiCard from "../components/KpiCard";
import {
  CardSkeleton,
  ChartSkeleton,
  TableSkeleton,
} from "../components/Skeleton";
import {
  Zap,
  PlugZap,
  Sun,
  IndianRupee,
  PiggyBank,
  Fuel,
  TrendingUp,
  BarChart3,
  AlertCircle,
  Calendar,
  LayoutDashboard,
  ArrowUpDown,
  Table2,
} from "lucide-react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
  Legend,
} from "recharts";

const CHART_COLORS = {
  grid: "#475569",
  solar: "#f59e0b",
  total: "#2563eb",
  cost: "#64748b",
  savings: "#10b981",
};

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

function formatDateForTable(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  if (isNaN(d)) return dateStr;
  const day = String(d.getDate()).padStart(2, "0");
  const month = d.toLocaleString("en-IN", { month: "short" });
  return `${day}-${month}-${d.getFullYear()}`;
}

function formatDayForTable(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  if (isNaN(d)) return "";
  return d.toLocaleString("en-IN", { weekday: "long" });
}

function formatTimeForTable(value) {
  if (value == null || value === "") return "";
  const text = String(value).trim();
  const timeMatch = text.match(/(\d{1,2}):(\d{2})/);
  if (timeMatch) {
    const hh = String(Math.min(23, Number(timeMatch[1]))).padStart(2, "0");
    return `${hh}:${timeMatch[2]}`;
  }

  const d = new Date(text);
  if (!isNaN(d)) {
    return d.toLocaleTimeString("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  }
  return text.slice(0, 5);
}

function formatIssueText(value) {
  if (value == null) return "No issues";
  const text = String(value).trim();
  if (!text) return "No issues";
  const lower = text.toLowerCase();
  return `${lower.charAt(0).toUpperCase()}${lower.slice(1)}`;
}

const PAGE_SIZE = 15;

export default function Overview() {
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
        day: formatDayForTable(row["Date"]),
        time: formatTimeForTable(row["Time"]),
        ambientTemperature: row["Ambient Temperature °C"] ?? "",
        grid: row["Grid Units Consumed (KWh)"] ?? 0,
        solar: row["Solar Units Consumed(KWh)"] ?? 0,
        total: row["Total Units Consumed (KWh)"] ?? 0,
        cost: row["Total Units Consumed in INR"] ?? 0,
        savings: row["Energy Saving in INR"] ?? 0,
        panelsCleaned: row["Number of Panels Cleaned"] ?? "",
        dieselConsumed: row["Diesel consumed"] ?? "",
        waterThroughStp: row["Water treated through STP"] ?? "",
        waterThroughWtp: row["Water treated through WTP"] ?? "",
        issues: row["Issues"] ?? "",
      })),
    [unified],
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

  return (
    <div className="px-8 py-6 bg-gray-100 rounded-3xl h-[calc(100vh-2rem)] flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between shrink-0 mb-6">
        <div>
          <h1 className="text-xl font-semibold text-slate-900 flex items-center gap-2">
            <LayoutDashboard className="w-5 h-5 text-blue-600" />
            Overview
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Energy consumption, costs, and savings at a glance
          </p>
        </div>
        {dateRange && (
          <div className="flex items-center gap-1.5 text-xs text-slate-400">
            <Calendar className="w-3.5 h-3.5" />
            {dateRange.min_date} — {dateRange.max_date}
            <span className="bg-white rounded-xl px-2 border border-slate-200 ">
              {chartData.length} records
            </span>
          </div>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2.5 text-sm text-red-600 border border-red-200 bg-red-50 px-5 py-3 rounded-lg mb-6 shrink-0">
          <AlertCircle className="w-4 h-4 shrink-0" />
          Failed to load: {error.message}
        </div>
      )}

      {/* KPI Grid */}
      <div className="grid grid-cols-3 gap-4 shrink-0 mb-6">
        {kpisLoading ? (
          Array.from({ length: 6 }).map((_, i) => <CardSkeleton key={i} />)
        ) : (
          <>
            <KpiCard
              label="Total Units Consumed"
              value={kpis?.total_energy_kwh}
              unit="KWh"
              icon={Zap}
              accent="text-blue-600"
              iconBg="bg-blue-50"
            />
            <KpiCard
              label="Grid Units Consumed"
              value={kpis?.total_grid_kwh}
              unit="KWh"
              icon={PlugZap}
              accent="text-slate-700"
              iconBg="bg-slate-100"
            />
            <KpiCard
              label="Solar Units Consumed"
              value={kpis?.total_solar_kwh}
              unit="KWh"
              icon={Sun}
              accent="text-amber-600"
              iconBg="bg-amber-50"
            />
            <KpiCard
              label="Total Cost"
              value={kpis?.total_cost_inr}
              unit="INR"
              icon={IndianRupee}
              accent="text-slate-700"
              iconBg="bg-slate-100"
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
              label="Diesel Consumed"
              value={kpis?.diesel_consumed_liters}
              unit="L"
              icon={Fuel}
              accent="text-red-600"
              iconBg="bg-red-50"
            />
          </>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto space-y-6">
        {/* Charts */}
        {dataLoading ? (
          <div className="flex items-stretch gap-4">
            <div className="flex-1 min-w-0">
              <ChartSkeleton />
            </div>
            <div className="flex-1 min-w-0">
              <ChartSkeleton />
            </div>
          </div>
        ) : (
          <div className="flex items-stretch gap-4">
            {/* Energy Trend */}
            <section className="flex-1 min-w-0 bg-white rounded-lg border border-slate-200 overflow-hidden">
              <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <TrendingUp className="w-4 h-4 text-blue-600" />
                  <h2 className="text-sm font-medium text-slate-700">
                    Energy Consumption Trend
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
                  <LineChart data={chartData}>
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
                      wrapperStyle={{
                        fontSize: 12,
                        paddingTop: 8,
                      }}
                    />
                    <Line
                      type="monotone"
                      dataKey="grid"
                      name="Grid Units Consumed (KWh)"
                      stroke={CHART_COLORS.grid}
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="solar"
                      name="Solar Units Consumed (KWh)"
                      stroke={CHART_COLORS.solar}
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="total"
                      name="Total Units Consumed (KWh)"
                      stroke={CHART_COLORS.total}
                      strokeWidth={2}
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </section>

            {/* Cost vs Savings */}
            <section className="flex-1 min-w-0 bg-white rounded-lg border border-slate-200 overflow-hidden">
              <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <BarChart3 className="w-4 h-4 text-blue-600" />
                  <h2 className="text-sm font-medium text-slate-700">
                    Cost vs Savings (INR)
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
                  <BarChart data={chartData}>
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
                      wrapperStyle={{
                        fontSize: 12,
                        paddingTop: 8,
                      }}
                    />
                    <Bar
                      dataKey="cost"
                      name="Total Units Consumed in INR"
                      fill={CHART_COLORS.cost}
                      radius={[4, 4, 0, 0]}
                    />
                    <Bar
                      dataKey="savings"
                      name="Energy Saving in INR"
                      fill={CHART_COLORS.savings}
                      radius={[4, 4, 0, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>
          </div>
        )}

        {/* Data Table */}
        {dataLoading ? (
          <TableSkeleton rows={4} cols={14} />
        ) : (
          <section className="bg-white rounded-lg border border-slate-200">
            <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Table2 className="w-4 h-4 text-blue-600" />
                <h2 className="text-sm font-medium text-slate-700">
                  Daily Energy Data
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
                    {[
                      { key: "date", label: "Date" },
                      { key: "day", label: "Day" },
                      { key: "time", label: "Time" },
                      {
                        key: "ambientTemperature",
                        label: "Ambient Temperature (°C)",
                      },
                      {
                        key: "grid",
                        label: "Grid Units Consumed (kWh)",
                      },
                      {
                        key: "solar",
                        label: "Solar Units Consumed (kWh)",
                      },
                      {
                        key: "total",
                        label: "Total Units Consumed (kWh)",
                      },
                      {
                        key: "cost",
                        label: "Total Cost (INR)",
                      },
                      {
                        key: "savings",
                        label: "Solar Cost Savings (INR)",
                      },
                      {
                        key: "panelsCleaned",
                        label: "Panels Cleaned",
                      },
                      {
                        key: "dieselConsumed",
                        label: "Diesel Consumed (Litres)",
                      },
                      {
                        key: "waterThroughStp",
                        label: "Water Treated through STP (kilo Litres)",
                      },
                      {
                        key: "waterThroughWtp",
                        label: "Water Treated through WTP (kilo Litres)",
                      },
                      { key: "issues", label: "Issues" },
                    ].map((col) => (
                      <th
                        key={col.key}
                        onClick={() => toggleSort(col.key)}
                        className="px-5 py-3 text-xs font-medium text-slate-500 uppercase tracking-wide cursor-pointer select-none hover:text-slate-700 whitespace-nowrap"
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
                      <td className="px-5 py-3 text-slate-700">
                        {formatDateForTable(row.date)}
                      </td>
                      <td className="px-5 py-3 text-slate-700">{row.day}</td>
                      <td className="px-5 py-3 text-slate-700">{row.time}</td>
                      <td className="px-5 py-3 text-slate-700">
                        {row.ambientTemperature || "—"}
                      </td>
                      <td className="px-5 py-3 text-slate-700">
                        {formatNumber(row.grid)}
                      </td>
                      <td className="px-5 py-3 text-slate-700">
                        {formatNumber(row.solar)}
                      </td>
                      <td className="px-5 py-3 text-slate-700">
                        {formatNumber(row.total)}
                      </td>
                      <td className="px-5 py-3 text-slate-700">
                        {formatNumber(row.cost)}
                      </td>
                      <td className="px-5 py-3 text-slate-700">
                        {formatNumber(row.savings)}
                      </td>
                      <td className="px-5 py-3 text-slate-700">
                        {row.panelsCleaned === "" || row.panelsCleaned == null
                          ? "—"
                          : formatNumber(row.panelsCleaned)}
                      </td>
                      <td className="px-5 py-3 text-slate-700">
                        {row.dieselConsumed === "" || row.dieselConsumed == null
                          ? "—"
                          : formatNumber(row.dieselConsumed)}
                      </td>
                      <td className="px-5 py-3 text-slate-700">
                        {row.waterThroughStp === "" ||
                        row.waterThroughStp == null
                          ? "—"
                          : formatNumber(row.waterThroughStp)}
                      </td>
                      <td className="px-5 py-3 text-slate-700">
                        {row.waterThroughWtp === "" ||
                        row.waterThroughWtp == null
                          ? "—"
                          : formatNumber(row.waterThroughWtp)}
                      </td>
                      <td className="px-5 py-3 text-slate-700">
                        {formatIssueText(row.issues)}
                      </td>
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
