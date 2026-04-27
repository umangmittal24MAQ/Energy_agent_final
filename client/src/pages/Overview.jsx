import { useMemo, useState } from "react";
import { useKpis, useUnifiedData } from "../lib/hooks";
import {
  formatLongDate,
  formatNumber,
  parseNumeric,
  normalizeRowDateKey,
  sortRowsByDateAsc,
  PAGE_SIZE,
} from "../lib/utils";
import {
  COL,
  CHART_COLORS,
  DATE_LOCALE,
  NUMBER_LOCALE,
} from "../lib/constants";
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
import Solar from "./Solar";
import Grid from "./Grid";
import Diesel from "./Diesel";

const OVERVIEW_TABS = [
  { key: "daily", label: "Daily Report", icon: LayoutDashboard },
  { key: "solar", label: "Solar", icon: Sun },
  { key: "grid", label: "Grid", icon: PlugZap },
  { key: "diesel", label: "Diesel", icon: Fuel },
];

function formatDayForTable(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  if (isNaN(d)) return "";
  return d.toLocaleString(NUMBER_LOCALE, { weekday: "long" });
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

const COMPACT_TABLE_COLUMNS = [
  {
    key: "date",
    label: "Date",
    render: (row) => formatLongDate(row.date),
  },
  {
    key: "day",
    label: "Day",
    render: (row) => row.day || "—",
  },
  {
    key: "grid",
    label: "Grid Consumed (kWh)",
    render: (row) => formatNumber(row.grid),
  },
  {
    key: "solar",
    label: "Solar Generated (kWh)",
    render: (row) => formatNumber(row.solar),
  },
  {
    key: "total",
    label: "Total Consumed (kWh)",
    render: (row) => formatNumber(row.total),
  },
  {
    key: "cost",
    label: "Total Cost (INR)",
    render: (row) => formatNumber(row.cost),
  },
  {
    key: "savings",
    label: "Energy Cost Savings (INR)",
    render: (row) => formatNumber(row.savings),
  },
];

const EXTENDED_ONLY_COLUMNS = [
  {
    key: "time",
    label: "Time",
    render: (row) => row.time || "—",
  },
  {
    key: "ambientTemperature",
    label: "Ambient Temperature (°C)",
    render: (row) => formatNumber(row.ambientTemperature),
  },
  {
    key: "savings",
    label: "Energy Cost Savings (INR)",
    render: (row) => formatNumber(row.savings),
  },
  {
    key: "panelsCleaned",
    label: "Panels Cleaned",
    render: (row) => formatNumber(row.panelsCleaned),
  },
  {
    key: "dieselConsumed",
    label: "Diesel Consumed (Litres)",
    render: (row) => formatNumber(row.dieselConsumed),
  },
  {
    key: "waterThroughStp",
    label: "Water Treated through STP (kilo Litres)",
    render: (row) => formatNumber(row.waterThroughStp),
  },
  {
    key: "waterThroughWtp",
    label: "Water Treated through WTP (kilo Litres)",
    render: (row) => formatNumber(row.waterThroughWtp),
  },
  {
    key: "issues",
    label: "Issues",
    render: (row) => formatIssueText(row.issues),
  },
];

export default function Overview() {
  const { currentDateParam, currentDateLabel, defaultFilterStart } =
    useMemo(() => {
      const d = new Date();
      d.setHours(0, 0, 0, 0);
      const year = d.getFullYear();
      const month = String(d.getMonth() + 1).padStart(2, "0");
      const day = String(d.getDate()).padStart(2, "0");
      return {
        currentDateParam: `${year}-${month}-${day}`,
        currentDateLabel: d.toLocaleDateString(DATE_LOCALE, {
          year: "numeric",
          month: "long",
          day: "numeric",
        }),
        defaultFilterStart: `${year}-${month}-01`,
      };
    }, []);

  const {
    data: kpis,
    isLoading: kpisLoading,
    error: kpisError,
  } = useKpis({
    startDate: currentDateParam,
    endDate: currentDateParam,
  });
  const {
    data: unified,
    isLoading: dataLoading,
    error: dataError,
  } = useUnifiedData();

  const sourceRows = useMemo(() => unified?.data || [], [unified?.data]);

  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState("date");
  const [sortAsc, setSortAsc] = useState(false);
  const [activeTab, setActiveTab] = useState("daily");
  const [isExtendedView, setIsExtendedView] = useState(false);

  const dateRange = unified?.date_range || null;

  const availableMax = dateRange?.max_date
    ? normalizeRowDateKey(dateRange.max_date) || currentDateParam
    : currentDateParam;
  const availableMin = dateRange?.min_date
    ? normalizeRowDateKey(dateRange.min_date) || ""
    : "";
  const [filterStart, setFilterStart] = useState(defaultFilterStart);
  const [filterEnd, setFilterEnd] = useState("");
  const effectiveEnd = filterEnd || availableMax;
  const dieselConsumedToday = useMemo(() => {
    const todayRows = sourceRows.filter(
      (row) => normalizeRowDateKey(row[COL.DATE]) === currentDateParam,
    );
    if (todayRows.length === 0) return 0;
    const total = todayRows.reduce(
      (sum, row) => sum + parseNumeric(row[COL.DIESEL]),
      0,
    );
    return Math.ceil(total);
  }, [sourceRows, currentDateParam]);

  const allChartData = useMemo(
    () =>
      sourceRows.map((row) => ({
        date: row[COL.DATE],
        day: formatDayForTable(row[COL.DATE]),
        time: formatTimeForTable(row[COL.TIME]),
        ambientTemperature: row[COL.AMBIENT_TEMP] ?? "",
        grid: row[COL.GRID_UNITS] ?? 0,
        solar: row[COL.SOLAR_UNITS] ?? 0,
        total: row[COL.TOTAL_UNITS] ?? 0,
        cost: row[COL.TOTAL_COST] ?? 0,
        savings: row[COL.ENERGY_SAVINGS] ?? 0,
        panelsCleaned: row[COL.PANELS_CLEANED] ?? "",
        dieselConsumed: row[COL.DIESEL] ?? "",
        waterThroughStp: row[COL.WATER_STP] ?? "",
        waterThroughWtp: row[COL.WATER_WTP] ?? "",
        issues: row[COL.ISSUES] ?? "",
      })),
    [sourceRows],
  );

  const chartData = useMemo(
    () =>
      allChartData.filter((row) => {
        const key = normalizeRowDateKey(row.date);
        if (!key) return false;
        return key >= filterStart && key <= effectiveEnd;
      }),
    [allChartData, filterStart, effectiveEnd],
  );

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

  const visibleColumns = useMemo(() => {
    if (isExtendedView) {
      return [...COMPACT_TABLE_COLUMNS, ...EXTENDED_ONLY_COLUMNS];
    }
    return COMPACT_TABLE_COLUMNS;
  }, [isExtendedView]);

  function toggleSort(key) {
    if (sortKey === key) {
      setSortAsc(!sortAsc);
    } else {
      setSortKey(key);
      setSortAsc(true);
    }
    setPage(0);
  }

  function renderTabPanel() {
    if (activeTab === "solar") {
      return <Solar embedded startDate={filterStart} endDate={effectiveEnd} />;
    }
    if (activeTab === "grid") {
      return <Grid embedded startDate={filterStart} endDate={effectiveEnd} />;
    }
    if (activeTab === "diesel") {
      return <Diesel embedded startDate={filterStart} endDate={effectiveEnd} />;
    }

    return (
      <div className="space-y-6">
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
          <div className="flex items-stretch gap-4 animate-scale-in">
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
                    as of {formatLongDate(dateRange.max_date)}
                  </span>
                )}
              </div>
              <div className="pr-5 pt-5">
                <ResponsiveContainer width="100%" height={360}>
                  <LineChart
                    data={trendChartData}
                    margin={{
                      top: 8,
                      right: 8,
                      left: 0,
                      bottom: 40,
                    }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis
                      dataKey="date"
                      tickFormatter={formatLongDate}
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
                      labelFormatter={formatLongDate}
                      contentStyle={{
                        borderRadius: 8,
                        border: "1px solid #e2e8f0",
                        fontSize: 12,
                        boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.1)",
                      }}
                    />
                    <Legend verticalAlign="bottom" height={36} />
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
                    as of {formatLongDate(dateRange.max_date)}
                  </span>
                )}
              </div>
              <div className="pr-5 pt-5">
                <ResponsiveContainer width="100%" height={360}>
                  <BarChart
                    data={trendChartData}
                    margin={{
                      top: 8,
                      right: 8,
                      left: 0,
                      bottom: 40,
                    }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis
                      dataKey="date"
                      tickFormatter={formatLongDate}
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
                      labelFormatter={formatLongDate}
                      contentStyle={{
                        borderRadius: 8,
                        border: "1px solid #e2e8f0",
                        fontSize: 12,
                        boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.1)",
                      }}
                    />
                    <Legend verticalAlign="bottom" height={36} />
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

        {dataLoading ? (
          <TableSkeleton rows={4} cols={6} />
        ) : (
          <section className="bg-white rounded-lg border border-slate-200 animate-slide-up">
            <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <Table2 className="w-4 h-4 text-blue-600" />
                <h2 className="text-sm font-medium text-slate-700">
                  Daily Energy Data
                </h2>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setIsExtendedView((prev) => !prev)}
                  className="px-3 py-1.5 text-xs font-medium rounded-md border border-slate-300 text-slate-700 hover:bg-slate-50 transition-colors cursor-pointer"
                >
                  {isExtendedView ? "Compact View" : "Extended View"}
                </button>
                <span className="text-xs text-slate-400">
                  {sorted.length} records
                </span>
              </div>
            </div>
            <div className="max-h-[70vh] overflow-y-auto overflow-x-hidden">
              <table className="energy-table w-full table-fixed text-sm text-left">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50">
                    {visibleColumns.map((col) => (
                      <th
                        key={col.key}
                        onClick={() => toggleSort(col.key)}
                        className="px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wide cursor-pointer select-none hover:text-slate-700 whitespace-normal wrap-break-word align-top sticky top-0 z-10 bg-slate-50"
                      >
                        <span className="inline-flex flex-wrap items-center gap-1 leading-tight">
                          {col.label}
                          <ArrowUpDown
                            className={`w-3 h-3 mt-0.5 ${sortKey === col.key ? "text-blue-600" : "text-slate-300"}`}
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
                      {visibleColumns.map((col) => (
                        <td
                          key={col.key}
                          className="px-4 py-3 text-slate-700 align-top whitespace-normal wrap-break-word"
                        >
                          {col.render(row)}
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
    );
  }

  const error = kpisError || dataError;

  return (
    <div className="px-8 py-6 bg-gray-100 rounded-3xl flex flex-col gap-6">
      <div className="flex items-center justify-between mb-2">
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
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <Calendar className="w-3.5 h-3.5 text-slate-400" />
            <input
              type="date"
              value={filterStart}
              min={availableMin}
              max={effectiveEnd}
              onChange={(e) => {
                setFilterStart(e.target.value);
                setPage(0);
              }}
              className="px-2 py-1 border border-slate-200 rounded-md bg-white text-xs text-slate-700 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
            />
            <span className="text-slate-400">—</span>
            <input
              type="date"
              value={effectiveEnd}
              min={filterStart}
              max={availableMax}
              onChange={(e) => {
                setFilterEnd(e.target.value);
                setPage(0);
              }}
              className="px-2 py-1 border border-slate-200 rounded-md bg-white text-xs text-slate-700 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
            />
            <span className="bg-white rounded-xl px-2 border border-slate-200">
              {chartData.length} records
            </span>
          </div>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2.5 text-sm text-red-600 border border-red-200 bg-red-50 px-5 py-3 rounded-lg">
          <AlertCircle className="w-4 h-4 shrink-0" />
          Failed to load: {error.message}
        </div>
      )}

      <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm text-blue-800">
        The Key Metrics shown below are of {currentDateLabel}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {kpisLoading ? (
          Array.from({ length: 6 }).map((_, i) => <CardSkeleton key={i} />)
        ) : (
          <>
            <KpiCard
              label="Total Units Consumed from all Energy Sources"
              value={kpis?.total_energy_kwh}
              unit="KWh"
              icon={Zap}
              accent="text-blue-600"
              iconBg="bg-blue-50"
              delay={0}
            />
            <KpiCard
              label="Grid Units Consumed"
              value={kpis?.total_grid_kwh}
              unit="KWh"
              icon={PlugZap}
              accent="text-slate-700"
              iconBg="bg-slate-100"
              delay={60}
            />
            <KpiCard
              label="Solar Units Consumed"
              value={kpis?.total_solar_kwh}
              unit="KWh"
              icon={Sun}
              accent="text-amber-600"
              iconBg="bg-amber-50"
              delay={120}
            />
            <KpiCard
              label="Total Cost Incurred"
              value={kpis?.total_cost_inr}
              unit="INR"
              icon={IndianRupee}
              accent="text-slate-700"
              iconBg="bg-slate-100"
              delay={180}
            />
            <KpiCard
              label="Solar Cost Savings"
              value={kpis?.solar_savings_inr}
              unit="INR"
              icon={PiggyBank}
              accent="text-emerald-600"
              iconBg="bg-emerald-50"
              delay={240}
            />
            <KpiCard
              label="Diesel Consumed"
              value={dieselConsumedToday}
              unit="L"
              icon={Fuel}
              accent="text-red-600"
              iconBg="bg-red-50"
              delay={300}
            />
          </>
        )}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white px-2">
        <div className="flex items-center [&>button]:cursor-pointer">
          {OVERVIEW_TABS.map((tab) => {
            const isActive = activeTab === tab.key;
            const Icon = tab.icon;
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`relative flex-1 px-4 py-3 text-sm font-medium transition-colors ${
                  isActive
                    ? "text-slate-900"
                    : "text-slate-500 hover:text-slate-700"
                }`}
              >
                <span className="inline-flex items-center justify-center gap-1.5">
                  <Icon
                    className={`w-3.5 h-3.5 ${
                      isActive ? "text-blue-600" : "text-slate-400"
                    }`}
                  />
                  {tab.label}
                </span>
                <span
                  className={`absolute left-3 right-3 bottom-0 h-0.5 rounded-full transition-all ${
                    isActive ? "bg-blue-500" : "bg-transparent"
                  }`}
                />
              </button>
            );
          })}
        </div>
      </div>

      {renderTabPanel()}
    </div>
  );
}
