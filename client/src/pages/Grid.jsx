import {
  CardSkeleton,
  ChartSkeleton,
  TableSkeleton,
} from "../components/Skeleton";
import { useState, useMemo } from "react";
import { useKpis, useUnifiedData } from "../lib/hooks";
import {
  formatLongDate,
  formatNumber,
  parseNumeric,
  getLocalDateKey,
  normalizeRowDateKey,
  sortRowsByDateAsc,
  PAGE_SIZE,
} from "../lib/utils";
import { COL, CHART_COLORS, CHART_AXIS } from "../lib/constants";
import KpiCard from "../components/KpiCard";
import {
  PlugZap,
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

export default function Grid({
  embedded = false,
  startDate: propStartDate,
  endDate: propEndDate,
}) {
  const { isLoading: kpisLoading, error: kpisError } = useKpis();
  const {
    data: rawData,
    isLoading: dataLoading,
    error: dataError,
  } = useUnifiedData();

  const dateRange = rawData?.date_range || null;
  const sourceRows = useMemo(() => rawData?.data || [], [rawData?.data]);

  // Date range: use props when embedded, otherwise full range
  const hasDateFilter = embedded && propStartDate && propEndDate;

  const metricValues = useMemo(() => {
    if (sourceRows.length === 0) {
      return {
        gridUnits: 0,
        gridContribution: "0",
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
      const todayKey = getLocalDateKey(new Date());
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

    const gridUnitsRaw = selectedRows.reduce(
      (sum, row) => sum + parseNumeric(row[COL.GRID_UNITS]),
      0,
    );
    const solarUnitsRaw = selectedRows.reduce(
      (sum, row) => sum + parseNumeric(row[COL.SOLAR_UNITS]),
      0,
    );
    const dieselUnitsRaw = selectedRows.reduce(
      (sum, row) => sum + parseNumeric(row[COL.DIESEL]),
      0,
    );

    const denominator = gridUnitsRaw + solarUnitsRaw + dieselUnitsRaw;
    const gridContribution =
      denominator > 0 ? ((gridUnitsRaw / denominator) * 100).toFixed(2) : "0";

    return {
      gridUnits: Math.ceil(gridUnitsRaw),
      gridContribution,
    };
  }, [sourceRows, hasDateFilter, propStartDate, propEndDate]);

  const allChartData = useMemo(
    () =>
      (rawData?.data || []).map((row) => ({
        date: row[COL.DATE],
        grid: row[COL.GRID_UNITS] ?? 0,
        total: row[COL.TOTAL_UNITS] ?? 0,
        cost: row[COL.TOTAL_COST] ?? 0,
      })),
    [rawData],
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

  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState("date");
  const [sortAsc, setSortAsc] = useState(false);

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
    {
      key: "grid",
      label: "Grid Units Consumed (KWh)",
      format: formatNumber,
    },
  ];

  const rootClass = embedded
    ? "space-y-6"
    : "px-8 py-6 space-y-6 bg-gray-100 rounded-3xl";

  return (
    <div className={rootClass}>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900 flex items-center gap-2">
            <PlugZap className="w-5 h-5 text-slate-600" />
            Grid
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Grid power consumption, costs, and daily usage breakdown
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

      {dataLoading ? (
        <ChartSkeleton />
      ) : (
        <section className="bg-white rounded-lg border border-slate-200 animate-scale-in">
          <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-slate-600" />
              <h2 className="text-sm font-medium text-slate-700">
                Grid Consumption Trend
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
                  <linearGradient id="gridGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop
                      offset="5%"
                      stopColor={CHART_COLORS.grid}
                      stopOpacity={0.15}
                    />
                    <stop
                      offset="95%"
                      stopColor={CHART_COLORS.grid}
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
                <Legend verticalAlign="bottom" height={36} />
                <Area
                  type="monotone"
                  dataKey="grid"
                  name="Grid Units Consumed (KWh)"
                  stroke={CHART_COLORS.grid}
                  strokeWidth={2}
                  fill="url(#gridGrad)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </section>
      )}

      {dataLoading ? (
        <TableSkeleton rows={4} cols={2} />
      ) : (
        <section className="bg-white rounded-lg border border-slate-200 animate-slide-up">
          <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <PlugZap className="w-4 h-4 text-slate-600" />
              <h2 className="text-sm font-medium text-slate-700">
                Daily Grid Data
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
  );
}
