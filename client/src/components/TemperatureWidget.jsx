/**
 * TemperatureWidget — Optimal Indoor Temperature card.
 * Mirrors the design language of WeatherWidget.jsx.
 * Usage: import { TemperatureWidget } from "../components/TemperatureWidget"
 */
import { useQuery } from "@tanstack/react-query";
import { Thermometer, Zap, Smile, RefreshCw, AlertTriangle, Moon } from "lucide-react";
import { fetchTemperatureRecommendation } from "../lib/api";

// ── Query hook ────────────────────────────────────────────────────────────────
export function useTemperatureRecommendation() {
  return useQuery({
    queryKey: ["temperature-recommendation"],
    queryFn: fetchTemperatureRecommendation,
    staleTime: 9 * 60 * 1000,          // server caches 10 min
    refetchInterval: 10 * 60 * 1000,   // auto-refresh every 10 min
    refetchOnWindowFocus: false,
    retry: 1,
  });
}

// ── Score badge ───────────────────────────────────────────────────────────────
function ScoreBadge({ label, score, color, icon: Icon }) {
  const width = Math.max(0, Math.min(100, score));
  return (
    <div className="rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-3 flex-1">
      <div className="flex items-center gap-1.5 mb-1">
        <Icon size={13} className={color} />
        <p className="text-[11px] text-slate-400 uppercase tracking-wide">{label}</p>
      </div>
      <p className="text-xl font-semibold text-slate-800">
        {score}
        <span className="text-xs font-normal text-slate-400 ml-0.5">/100</span>
      </p>
      <div className="mt-1.5 h-1.5 w-full rounded-full bg-slate-200">
        <div
          className="h-1.5 rounded-full transition-all duration-500"
          style={{ width: `${width}%`, backgroundColor: color.replace("text-", "") }}
        />
      </div>
    </div>
  );
}

// ── Main widget ───────────────────────────────────────────────────────────────
export function TemperatureWidget() {
  const { data, isLoading, error, refetch, isFetching } = useTemperatureRecommendation();

  if (isLoading) {
    return (
      <div className="bg-white rounded-lg border border-slate-200 p-5 animate-pulse">
        <div className="h-4 w-48 bg-slate-200 rounded mb-4" />
        <div className="h-20 bg-slate-100 rounded-lg mb-3" />
        <div className="grid grid-cols-2 gap-3">
          <div className="h-14 bg-slate-100 rounded-lg" />
          <div className="h-14 bg-slate-100 rounded-lg" />
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="bg-white rounded-lg border border-slate-200 px-5 py-4 flex items-center gap-2 text-sm text-slate-400">
        <AlertTriangle className="w-4 h-4 text-amber-400" />
        Indoor temperature recommendation unavailable — check OPENWEATHERMAP_API_KEY
      </div>
    );
  }

  return (
    <section className="bg-white rounded-lg border border-slate-200 animate-scale-in">
      {/* Header */}
      <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Thermometer size={16} className="text-blue-500" />
          <h2 className="text-sm font-medium text-slate-700">Optimal Indoor Temperature</h2>
          {data.is_night_mode && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-indigo-50 text-indigo-600 border border-indigo-200">
              <Moon size={10} />
              Night Mode
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {data.fetched_at && (
            <span className="text-xs text-slate-400">Updated {data.fetched_at}</span>
          )}
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="p-1 rounded hover:bg-slate-100 transition-colors disabled:opacity-40"
            title="Refresh recommendation"
          >
            <RefreshCw size={13} className={`text-slate-400 ${isFetching ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      <div className="p-5 space-y-4">
        {/* Recommendation hero */}
        <div className="rounded-lg bg-gradient-to-r from-blue-700 to-blue-500 px-5 py-4 text-white flex items-center justify-between">
          <div>
            <p className="text-xs text-blue-200 uppercase tracking-wide mb-1">Recommended Indoor Setting</p>
            <p className="text-3xl font-bold">{data.recommended_indoor_range}</p>
            <p className="text-xs text-blue-200 mt-1">
              Outdoor: {data.outdoor_temperature}°C &bull; Humidity: {data.humidity}%
            </p>
          </div>
          <Thermometer size={40} className="text-blue-300 shrink-0" />
        </div>

        {/* Scores row */}
        <div className="flex gap-3">
          <ScoreBadge
            label="Comfort"
            score={data.comfort_score}
            color="#3b82f6"
            icon={Smile}
          />
          <ScoreBadge
            label="Efficiency"
            score={data.energy_efficiency_score}
            color="#10b981"
            icon={Zap}
          />
        </div>

        {/* Insights */}
        {data.recommendations && data.recommendations.length > 0 && (
          <div className="space-y-2">
            {data.recommendations.slice(0, 3).map((tip, i) => (
              <div
                key={i}
                className="rounded-lg bg-slate-50 border border-slate-100 px-3 py-2.5 text-xs text-slate-600 leading-relaxed flex gap-2"
              >
                <span className="text-blue-400 shrink-0 mt-0.5">▶</span>
                <span>{tip}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}