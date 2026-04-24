import {
  CardSkeleton,
  ChartSkeleton,
  TableSkeleton,
} from "../components/Skeleton";
import { useState, useMemo } from "react";
import { useUnifiedData } from "../lib/hooks";
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
import {
  Sun,
  PiggyBank,
  TrendingUp,
  AlertCircle,
  Calendar,
  Zap,
  ArrowUpDown,
  Activity,
  Clock,
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

function normalizeInverterStatus(rawStatus) {
  const text = String(rawStatus || "")
    .trim()
    .toLowerCase();
  if (!text) return "OFFLINE";
  if (
    text.includes("fault") ||
    text.includes("error") ||
    text.includes("trip")
  ) {
    return "FAULT";
  }
  if (
    text === "on" ||
    text.includes(" on") ||
    text.includes("active") ||
    text.includes("online") ||
    text.includes("running") ||
    text.includes("ok")
  ) {
    return "ACTIVE";
  }
  if (
    text === "off" ||
    text.includes("offline") ||
    text.includes("down") ||
    text.includes("stop")
  ) {
    return "OFFLINE";
  }
  return "OFFLINE";
}

function resolveLiveStatus(statusValues) {
  const normalized = (statusValues || [])
    .map((value) => normalizeInverterStatus(value))
    .filter(Boolean);

  if (normalized.includes("FAULT")) return "FAULT";
  if (normalized.includes("ACTIVE")) return "ACTIVE";
  return "OFFLINE";
}

function formatGenerationValue(value) {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value.toFixed(3)} kWh`;
}

function findFirstValue(row, keys) {
  for (const key of keys) {
    if (row && row[key] != null && String(row[key]).trim() !== "") {
      return row[key];
    }
  }
  return null;
}

function hasMeaningfulLiveValue(value) {
  if (value == null) return false;
  const text = String(value).trim();
  if (!text) return false;
  if (text.toLowerCase() === "nan") return false;
  return true;
}

function rowHasLiveInverterData(row) {
  if (!row) return false;
  for (let i = 1; i <= INVERTER_COUNT; i += 1) {
    const candidates = [
      `SMB${i}`,
      `SMB ${i}`,
      `SMB_${i}`,
      `Inverter${i}`,
      `Inverter ${i}`,
      `Inverter_${i}`,
      `SMB${i}_status`,
      `SMB${i} status`,
      `SMB${i} Status`,
      `Inverter${i}_status`,
      `Inverter${i} status`,
      `Inverter${i} Status`,
    ];
    if (candidates.some((key) => hasMeaningfulLiveValue(row[key]))) {
      return true;
    }
  }
  return false;
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
        solar: row[COL.SOLAR_UNITS] ?? 0,
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

  const latestSolarRow = useMemo(() => {
    if (!sourceRows.length) return null;
    const sorted = [...sourceRows].sort((a, b) => {
      const aKey = `${normalizeRowDateKey(a[COL.DATE]) || ""}-${String(a[COL.TIME] || "")}`;
      const bKey = `${normalizeRowDateKey(b[COL.DATE]) || ""}-${String(b[COL.TIME] || "")}`;
      return aKey.localeCompare(bKey);
    });

    for (let i = sorted.length - 1; i >= 0; i -= 1) {
      if (rowHasLiveInverterData(sorted[i])) {
        return sorted[i];
      }
    }

    return sorted[sorted.length - 1] || null;
  }, [sourceRows]);

  const inverterInsights = useMemo(() => {
    const fallbackRows = Array.from({ length: INVERTER_COUNT }, (_, idx) => ({
      id: `INV_${idx + 1}`,
      status: "OFFLINE",
      unitsGenerated: null,
      lastUpdated: "—",
    }));

    if (!latestSolarRow) {
      return {
        cards: fallbackRows,
        rows: fallbackRows,
        hasPerInverterGeneration: false,
        aggregateGeneration: null,
      };
    }

    const lastUpdatedDate = formatLongDate(latestSolarRow[COL.DATE]);
    const timeText = String(latestSolarRow[COL.TIME] || "").trim();
    const lastUpdated =
      [lastUpdatedDate, timeText].filter(Boolean).join(" ") || "—";

    const rows = Array.from({ length: INVERTER_COUNT }, (_, idx) => {
      const i = idx + 1;
      const smbStatusRaw = findFirstValue(latestSolarRow, [
        `SMB${i}_status`,
        `SMB${i} status`,
        `SMB${i} Status`,
        `SMB ${i}_status`,
        `SMB ${i} status`,
        `SMB ${i} Status`,
      ]);

      const inverterStatusRaw = findFirstValue(latestSolarRow, [
        `Inverter${i}_status`,
        `Inverter${i} status`,
        `Inverter${i} Status`,
        `INV_${i}_status`,
      ]);

      const unitsRaw = findFirstValue(latestSolarRow, [
        `SMB${i}`,
        `SMB ${i}`,
        `SMB_${i}`,
        `Inverter${i}`,
        `Inverter ${i}`,
        `Inverter_${i}`,
        `Inverter${i} Units Generated (kWh)`,
        `Inverter${i} Day Generation (kWh)`,
        `INV_${i} Units Generated (kWh)`,
        `INV_${i}_kwh`,
        `INV_${i}_day_generation_kwh`,
      ]);

      const smbStatus = normalizeInverterStatus(smbStatusRaw);
      const inverterStatus = normalizeInverterStatus(inverterStatusRaw);

      return {
        id: `INV_${i}`,
        smbId: `SMB${i}`,
        status: resolveLiveStatus([smbStatusRaw, inverterStatusRaw]),
        smbStatus,
        inverterStatus,
        unitsGenerated: parseNumeric(unitsRaw),
        lastUpdated,
      };
    });

    const hasPerInverterGeneration = rows.some(
      (row) => row.unitsGenerated != null && row.unitsGenerated >= 0,
    );

    return {
      cards: rows,
      rows,
      hasPerInverterGeneration,
      aggregateGeneration: parseNumeric(latestSolarRow[COL.DAY_GENERATION]),
    };
  }, [latestSolarRow]);

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
    {
      key: "solar",
      label: "Solar Units Consumed (KWh)",
      format: formatNumber,
    },
    {
      key: "savings",
      label: "Solar Cost Saving (INR)",
      format: formatNumber,
    },
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

        {/* ADDED: Inverter status cards (live). */}
        <section className="bg-white rounded-lg border border-slate-200 animate-slide-up">
          <div className="px-5 py-4 border-b border-slate-200 flex items-center gap-2">
            <Activity className="w-4 h-4 text-amber-600" />
            <h2 className="text-sm font-medium text-slate-700">
              Inverter Status (Live)
            </h2>
          </div>
          <div className="p-5 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
            {inverterInsights.cards.map((item) => {
              const badgeClass =
                item.status === "ACTIVE"
                  ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                  : item.status === "FAULT"
                    ? "bg-red-50 text-red-700 border-red-200"
                    : "bg-slate-50 text-slate-600 border-slate-200";

              return (
                <div
                  key={item.id}
                  className="rounded-lg border border-slate-200 p-3 bg-slate-50/60"
                >
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-xs font-semibold text-slate-500">
                      {item.id}
                    </p>
                    <p className="text-[10px] font-medium text-slate-400">
                      {item.smbId}
                    </p>
                  </div>
                  <span
                    className={`mt-2 inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${badgeClass}`}
                  >
                    {item.status}
                  </span>

                  <p className="mt-2 text-sm font-semibold text-slate-800">
                    {formatGenerationValue(item.unitsGenerated)}
                  </p>

                  <div className="mt-2 space-y-1 text-[10px] text-slate-500">
                    <p>SMB Status: {item.smbStatus}</p>
                    <p>Inverter Status: {item.inverterStatus}</p>
                  </div>
                </div>
              );
            })}
          </div>
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
