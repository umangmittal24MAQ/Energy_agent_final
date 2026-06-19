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
  PAGE_SIZE,
} from "../lib/utils";
import { COL, CHART_COLORS, CHART_AXIS } from "../lib/constants";
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

function safeNumber(value) {
  return Number.isNaN(value) || value == null ? 0 : value;
}

function formatDieselWithUnit(value) {
  const formatted = formatNumber(value);
  if (formatted === "—") return formatted;

  const numeric = parseNumeric(value);
  const unit = numeric === 0 || numeric === 1 ? "Litre" : "Litres";
  return `${formatted} ${unit}`;
}

export default function Diesel({
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
  const todayDateKey = useMemo(() => getLocalDateKey(new Date()), []);

  // Standalone date range state (only used when not embedded)
  const defaultStart = useMemo(() => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
  }, []);
  const availableMax = dateRange?.max_date
    ? normalizeRowDateKey(dateRange.max_date) || todayDateKey
    : todayDateKey;
  const availableMin = dateRange?.min_date
    ? normalizeRowDateKey(dateRange.min_date) || ""
    : "";

  const [ownStartDate, setOwnStartDate] = useState(defaultStart);
  const [ownEndDate, setOwnEndDate] = useState("");
  const ownEffectiveEnd = ownEndDate || availableMax;

  // Use props when embedded, own state when standalone
  const startDate = embedded && propStartDate ? propStartDate : ownStartDate;
  const endDate = embedded && propEndDate ? propEndDate : ownEffectiveEnd;

  const allChartData = useMemo(() => {
    const sanitized = sourceRows.map((row) => ({
      date: row[COL.DATE],
      diesel: safeNumber(parseNumeric(row[COL.DIESEL])),
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

  const chartData = useMemo(() => {
    return allChartData.filter((row) => {
      const key = normalizeRowDateKey(row.date);
      if (!key) return false;
      return key >= startDate && key <= endDate;
    });
  }, [allChartData, startDate, endDate]);

  const dieselMetrics = useMemo(() => {
    const filteredRows = sourceRows.filter((row) => {
      const key = normalizeRowDateKey(row[COL.DATE]);
      if (!key) return false;
      return key >= startDate && key <= endDate;
    });

    const dieselUnitsRaw = filteredRows.reduce(
      (sum, row) => sum + parseNumeric(row[COL.DIESEL]),
      0,
    );
    const gridUnitsRaw = filteredRows.reduce(
      (sum, row) => sum + parseNumeric(row[COL.GRID_UNITS]),
      0,
    );
    const solarUnitsRaw = filteredRows.reduce(
      (sum, row) => sum + parseNumeric(row[COL.SOLAR_UNITS]),
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
  }, [sourceRows, startDate, endDate]);

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
    {
      key: "diesel",
      label: "Diesel Consumed (IN LITRES)",
      format: formatDieselWithUnit,
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
            <Fuel className="w-5 h-5 text-red-500" />
            Diesel
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Diesel consumption tracking and daily usage breakdown
          </p>
        </div>
        {!embedded && dateRange && (
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <Calendar className="w-3.5 h-3.5 text-slate-400" />
            <input
              type="date"
              value={ownStartDate}
              min={availableMin}
              max={ownEffectiveEnd}
              onChange={(e) => {
                setOwnStartDate(e.target.value);
                setPage(0);
              }}
              className="px-2 py-1 border border-slate-200 rounded-md bg-white text-xs text-slate-700 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
            />
            <span className="text-slate-400">—</span>
            <input
              type="date"
              value={ownEffectiveEnd}
              min={ownStartDate}
              max={availableMax}
              onChange={(e) => {
                setOwnEndDate(e.target.value);
                setPage(0);
              }}
              className="px-2 py-1 border border-slate-200 rounded-md bg-white text-xs text-slate-700 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
            />
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
          <div className="pr-5 pt-5">
            <ResponsiveContainer width="100%" height={360}>
              <AreaChart
                data={chartData}
                margin={{
                  top: 8,
                  right: 8,
                  left: 0,
                  bottom: 40,
                }}
              >
                <defs>
                  <linearGradient id="dieselGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop
                      offset="5%"
                      stopColor={CHART_COLORS.diesel}
                      stopOpacity={0.15}
                    />
                    <stop
                      offset="95%"
                      stopColor={CHART_COLORS.diesel}
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
                  allowDataOverflow={false}
                  domain={[0, "auto"]}
                  label={{
                    value: "Diesel Consumed (L)",
                    angle: -90,
                    position: "insideLeft",
                  }}
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
                  dataKey="diesel"
                  name="Diesel Consumed"
                  stroke={CHART_COLORS.diesel}
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

      {dataLoading ? (
        <TableSkeleton rows={4} cols={2} />
      ) : (
        <section className="bg-white rounded-lg border border-slate-200 animate-slide-up">
          <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Fuel className="w-4 h-4 text-red-500" />
              <h2 className="text-sm font-medium text-slate-700">
                Daily Diesel Data
              </h2>
            </div>
            <span className="bg-white rounded-xl px-2 border border-slate-200">
              {chartData.length} records
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
