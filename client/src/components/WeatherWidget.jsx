/**
 * WeatherWidget — live weather card for Noida with solar impact context.
 * Two sizes: full (Solar page) and compact (Overview header).
 */
import { useWeather } from "../lib/hooks";
import { Cloud, Droplets, Wind, Sunrise, Sunset, RefreshCw, AlertTriangle, Sun, CloudRain } from "lucide-react";

const IMPACT_CONFIG = {
  clear:    { color: "text-emerald-600", bg: "bg-emerald-50", border: "border-emerald-200", dot: "bg-emerald-400", label: "Optimal" },
  moderate: { color: "text-amber-600",   bg: "bg-amber-50",   border: "border-amber-200",   dot: "bg-amber-400",   label: "Moderate Impact" },
  heavy:    { color: "text-red-600",     bg: "bg-red-50",     border: "border-red-200",     dot: "bg-red-400",     label: "Heavy Impact" },
};

function WeatherIcon({ main, size = 16 }) {
  const m = (main || "").toLowerCase();
  if (m.includes("rain") || m.includes("drizzle") || m.includes("thunder"))
    return <CloudRain size={size} className="text-blue-500" />;
  if (m.includes("cloud"))
    return <Cloud size={size} className="text-slate-400" />;
  return <Sun size={size} className="text-amber-400" />;
}

/** Full-size card — for Solar page */
export function WeatherWidget() {
  const { data, isLoading, error, dataUpdatedAt, refetch, isFetching } = useWeather();

  if (isLoading) {
    return (
      <div className="bg-white rounded-lg border border-slate-200 p-5 animate-pulse">
        <div className="h-4 w-32 bg-slate-200 rounded mb-3" />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[1,2,3,4].map(i => <div key={i} className="h-14 bg-slate-100 rounded-lg" />)}
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="bg-white rounded-lg border border-slate-200 px-5 py-4 flex items-center gap-2 text-sm text-slate-400">
        <AlertTriangle className="w-4 h-4 text-amber-400" />
        Weather data unavailable — check OPENWEATHERMAP_API_KEY
      </div>
    );
  }

  const impact = IMPACT_CONFIG[data.solar_impact] || IMPACT_CONFIG.moderate;
  const lastFetched = data.fetched_at;

  return (
    <section className="bg-white rounded-lg border border-slate-200 animate-scale-in">
      {/* Header */}
      <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <WeatherIcon main={data.weather_main} size={16} />
          <h2 className="text-sm font-medium text-slate-700">Live Weather — Noida</h2>
          <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${impact.bg} ${impact.color} ${impact.border} border`}>
            <span className={`w-1.5 h-1.5 rounded-full ${impact.dot}`} />
            Solar: {impact.label}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {lastFetched && (
            <span className="text-xs text-slate-400">Updated {lastFetched}</span>
          )}
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="p-1 rounded hover:bg-slate-100 transition-colors disabled:opacity-40"
            title="Refresh weather"
          >
            <RefreshCw size={13} className={`text-slate-400 ${isFetching ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      <div className="p-5 space-y-4">
        {/* Main metrics row */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <MetricTile
            label="Temperature"
            value={`${data.temp_c}°C`}
            sub={`Feels like ${data.feels_like_c}°C`}
            icon={<Sun size={14} className="text-amber-400" />}
          />
          <MetricTile
            label="Humidity"
            value={`${data.humidity_pct}%`}
            sub={data.humidity_pct > 70 ? "High — may reduce irradiance" : "Normal range"}
            icon={<Droplets size={14} className="text-blue-400" />}
          />
          <MetricTile
            label="Cloud Cover"
            value={`${data.cloud_cover_pct}%`}
            sub={data.weather_desc}
            icon={<Cloud size={14} className="text-slate-400" />}
          />
          <MetricTile
            label="Wind Speed"
            value={`${data.wind_speed_ms} m/s`}
            sub="Surface wind"
            icon={<Wind size={14} className="text-teal-400" />}
          />
        </div>

        {/* Sunrise / sunset row */}
        <div className="flex items-center gap-6 text-xs text-slate-500">
          <span className="flex items-center gap-1">
            <Sunrise size={13} className="text-orange-400" />
            Sunrise {data.sunrise}
          </span>
          <span className="flex items-center gap-1">
            <Sunset size={13} className="text-orange-500" />
            Sunset {data.sunset}
          </span>
          <span className="text-slate-400">{data.daylight_hrs}h daylight today</span>
        </div>

        {/* Impact reason banner */}
        <div className={`rounded-lg px-4 py-3 text-xs ${impact.bg} ${impact.color} border ${impact.border}`}>
          <span className="font-medium">Solar Outlook: </span>{data.impact_reason}
        </div>
      </div>
    </section>
  );
}

function MetricTile({ label, value, sub, icon }) {
  return (
    <div className="rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-3">
      <div className="flex items-center gap-1.5 mb-1">
        {icon}
        <p className="text-[11px] text-slate-400 uppercase tracking-wide">{label}</p>
      </div>
      <p className="text-lg font-semibold text-slate-800">{value}</p>
      <p className="text-[11px] text-slate-400 mt-0.5 leading-tight">{sub}</p>
    </div>
  );
}

/** Compact badge — for Overview header bar */
export function WeatherBadge() {
  const { data, isLoading } = useWeather();

  if (isLoading || !data) return null;

  const impact = IMPACT_CONFIG[data.solar_impact] || IMPACT_CONFIG.moderate;

  return (
    <div className="flex items-center gap-2 text-xs text-slate-500 border border-slate-200 rounded-lg px-3 py-1.5 bg-white">
      <WeatherIcon main={data.weather_main} size={13} />
      <span className="font-medium text-slate-700">{data.temp_c}°C</span>
      <span className="text-slate-300">|</span>
      <Droplets size={12} className="text-blue-400" />
      <span>{data.humidity_pct}%</span>
      <span className="text-slate-300">|</span>
      <Cloud size={12} className="text-slate-400" />
      <span>{data.cloud_cover_pct}%</span>
      <span className="text-slate-300">|</span>
      <span className={`flex items-center gap-1 font-medium ${impact.color}`}>
        <span className={`w-1.5 h-1.5 rounded-full ${impact.dot}`} />
        Solar: {impact.label}
      </span>
    </div>
  );
}