import { TrendingUp, TrendingDown, Minus, Info, CloudSun } from "lucide-react";

function ProgressBar({ pct, color }) {
  return (
    <div className="w-full h-2 bg-slate-100 rounded-full overflow-hidden">
      <div
        className={`h-full rounded-full transition-all duration-700 ${color}`}
        style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
      />
    </div>
  );
}

export default function SolarExpectedActual({ actualKwh = 0, weather = null }) {
  if (!weather) return null;

  const expected = weather.expected_so_far_kwh;
  const actual = actualKwh;
  const dayElapsed = weather.day_elapsed_pct;

  const performancePct = expected > 0 ? Math.round((actual / expected) * 100) : null;

  let status, statusColor, StatusIcon, barColor;
  if (performancePct === null || actual === 0 || dayElapsed < 2) {
    status = dayElapsed < 2 ? "Before sunrise — generation not started" : "Today's live data not yet available";
    statusColor = "text-slate-400";
    StatusIcon = Minus;
    barColor = "bg-slate-300";
  } else if (performancePct >= 90) {
    status = "On track";
    statusColor = "text-emerald-600";
    StatusIcon = TrendingUp;
    barColor = "bg-emerald-400";
  } else if (performancePct >= 65) {
    status = "Slightly below expected";
    statusColor = "text-amber-600";
    StatusIcon = Minus;
    barColor = "bg-amber-400";
  } else {
    status = "Underperforming";
    statusColor = "text-red-600";
    StatusIcon = TrendingDown;
    barColor = "bg-red-400";
  }

  const shortfall = expected > 0 ? Math.max(0, expected - actual) : 0;

  // Build factors that affect the expected generation estimate
  const factors = buildFactors(weather);

  return (
    <section className="bg-white rounded-lg border border-slate-200 animate-scale-in">
      <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <TrendingUp className="w-4 h-4 text-amber-600" />
          <h2 className="text-sm font-medium text-slate-700">Expected vs Actual — Today</h2>
        </div>
        <span className="text-xs text-slate-400">{dayElapsed}% of day elapsed</span>
      </div>

      <div className="p-5 space-y-5">
        {/* Main numbers — 2 cols now */}
        <div className="grid grid-cols-2 gap-3">
          <StatBlock
            label="Expected so far"
            value={`${expected.toLocaleString()} kWh`}
            sub="Weather-derived estimate"
            valueClass="text-slate-700"
          />
          <StatBlock
            label="Actual so far"
            value={`${Math.round(actual).toLocaleString()} kWh`}
            sub="Live from inverters"
            valueClass="text-amber-600"
          />
        </div>

        {/* Progress bar */}
        {performancePct !== null && dayElapsed >= 2 && (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-500">Performance vs expected</span>
              <span className={`font-semibold ${statusColor}`}>{performancePct}%</span>
            </div>
            <ProgressBar pct={performancePct} color={barColor} />
          </div>
        )}

        {/* Status row */}
        <div className={`flex items-center gap-2 text-sm font-medium ${statusColor}`}>
          <StatusIcon size={15} />
          {status}
          {shortfall > 0 && performancePct !== null && (
            <span className="text-xs font-normal text-slate-400 ml-1">
              — {Math.round(shortfall).toLocaleString()} kWh shortfall so far
            </span>
          )}
        </div>

        {/* Factors affecting expected estimate */}
        {factors.length > 0 && (
          <div className="rounded-lg bg-slate-50 border border-slate-100 px-4 py-3 space-y-2">
            <div className="flex items-center gap-1.5 text-xs font-medium text-slate-500 mb-1">
              <CloudSun size={13} className="text-amber-500" />
              Factors affecting today's estimate
            </div>
            <ul className="space-y-1.5">
              {factors.map((f, i) => (
                <li key={i} className="flex items-start gap-2 text-xs text-slate-600">
                  <span className={`mt-0.5 w-1.5 h-1.5 rounded-full shrink-0 ${f.dot}`} />
                  {f.text}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

function buildFactors(weather) {
  const factors = [];

  // Cloud cover
  if (weather.cloud_cover_pct != null) {
    const cc = weather.cloud_cover_pct;
    if (cc >= 70)
      factors.push({ dot: "bg-red-400", text: `Heavy cloud cover (${cc}%) — significantly reducing irradiance` });
    else if (cc >= 40)
      factors.push({ dot: "bg-amber-400", text: `Partial cloud cover (${cc}%) — moderately reducing solar input` });
    else if (cc <= 10)
      factors.push({ dot: "bg-emerald-400", text: `Clear sky (${cc}% cloud cover) — optimal irradiance conditions` });
    else
      factors.push({ dot: "bg-slate-300", text: `Light cloud cover (${cc}%) — minimal impact on generation` });
  }

  // Temperature
  if (weather.temp_c != null) {
    const t = weather.temp_c;
    if (t >= 40)
      factors.push({ dot: "bg-red-400", text: `High ambient temperature (${t}°C) — panel efficiency drops ~${Math.round((t - 25) * 0.4)}% above 25°C` });
    else if (t >= 35)
      factors.push({ dot: "bg-amber-400", text: `Warm conditions (${t}°C) — slight efficiency reduction expected` });
    else if (t <= 20)
      factors.push({ dot: "bg-emerald-400", text: `Cool temperature (${t}°C) — panels operating near peak efficiency` });
  }

  // Humidity
  if (weather.humidity_pct != null) {
    const h = weather.humidity_pct;
    if (h >= 80)
      factors.push({ dot: "bg-amber-400", text: `High humidity (${h}%) — haze may diffuse direct irradiance` });
    else if (h <= 30)
      factors.push({ dot: "bg-emerald-400", text: `Low humidity (${h}%) — clear atmosphere, good for generation` });
  }

  // Wind (helps cooling panels)
  if (weather.wind_kph != null) {
    const w = weather.wind_kph;
    if (w >= 20)
      factors.push({ dot: "bg-emerald-400", text: `Strong wind (${w} km/h) — natural panel cooling improves output` });
  }

  // Day elapsed context
  if (weather.day_elapsed_pct != null) {
    const pct = weather.day_elapsed_pct;
    if (pct < 20)
      factors.push({ dot: "bg-slate-300", text: `Early in the day (${pct}% elapsed) — generation ramp-up phase` });
    else if (pct > 80)
      factors.push({ dot: "bg-slate-300", text: `Late in the day (${pct}% elapsed) — irradiance declining` });
  }

  // Fallback if weather object has impact_reason but no granular fields
  if (factors.length === 0 && weather.impact_reason) {
    factors.push({ dot: "bg-slate-300", text: weather.impact_reason });
  }

  return factors;
}

function StatBlock({ label, value, sub, valueClass }) {
  return (
    <div className="rounded-lg bg-slate-50/60 border border-slate-100 px-3 py-3">
      <p className="text-[11px] text-slate-400 uppercase tracking-wide mb-1">{label}</p>
      <p className={`text-base font-bold ${valueClass}`}>{value}</p>
      <p className="text-[11px] text-slate-400 mt-0.5">{sub}</p>
    </div>
  );
}