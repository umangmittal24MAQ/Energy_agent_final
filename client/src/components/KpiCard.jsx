import { NUMBER_LOCALE } from "../lib/constants";

function formatValue(value) {
    if (value == null) return "—";
    const numeric = typeof value === "number" ? value : Number(value);
    if (Number.isFinite(numeric)) {
        return Math.ceil(numeric).toLocaleString(NUMBER_LOCALE, {
            maximumFractionDigits: 0,
        });
    }
    return value;
}

export default function KpiCard({
    label,
    value,
    unit,
    icon: Icon,
    accent = "text-slate-900",
    iconBg = "bg-slate-100",
    delay = 0,
}) {
    return (
        <div
            className="bg-white rounded-lg border border-slate-200 p-5 flex items-start gap-4 animate-slide-up hover:border-slate-300 transition-colors duration-200"
            style={delay ? { animationDelay: `${delay}ms` } : undefined}
        >
            {Icon && (
                <div
                    className={`w-10 h-10 rounded-lg ${iconBg} flex items-center justify-center shrink-0`}
                >
                    <Icon className={`w-5 h-5 ${accent}`} strokeWidth={1.8} />
                </div>
            )}
            <div className="min-w-0">
                <p className="text-xs font-medium text-slate-500 tracking-wide">
                    {label}
                </p>
                <p className={`text-2xl font-semibold mt-0.5 ${accent}`}>
                    {formatValue(value)}
                    {unit && (
                        <span className="text-sm font-normal text-slate-400 ml-1">
                            {unit}
                        </span>
                    )}
                </p>
            </div>
        </div>
    );
}
