import {
    DatabaseZap,
    LayoutDashboard,
    Sun,
    PlugZap,
    Fuel,
    CalendarClock,
} from "lucide-react";

const NAV_ITEMS = [
    { key: "overview", label: "Overview", icon: LayoutDashboard },
    { key: "solar", label: "Solar", icon: Sun },
    { key: "grid", label: "Grid", icon: PlugZap },
    { key: "diesel", label: "Diesel", icon: Fuel },
    { key: "scheduler", label: "Scheduler", icon: CalendarClock },
];

export default function Sidebar({ active, onNavigate }) {
    return (
        <aside className="w-60 shrink-0 bg-gray-100 flex flex-col rounded-3xl sticky top-4 h-[calc(100vh-2rem)] self-start">
            <div className="px-5 py-4 flex items-center gap-2 ">
                <div className="p-1 rounded-lg flex items-center justify-center">
                    <DatabaseZap className=" text-blue-600" strokeWidth={2.5} />
                </div>
                <span className="text-sm font-semibold text-slate-900 tracking-tight">
                    Energy Dashboard
                </span>
            </div>
            <span className="px-5 py-2 text-xs font-semibold text-slate-500 uppercase tracking-wider flex">
                Menu
            </span>
            <nav className="flex-1 py-2 space-y-0.5">
                {NAV_ITEMS.map((item) => {
                    const Icon = item.icon;
                    const isActive = active === item.key;
                    return (
                        <button
                            key={item.key}
                            onClick={() => onNavigate(item.key)}
                            className={`w-full relative flex items-center gap-2.5 px-5 py-2 cursor-pointer transition-all duration-200 ${
                                isActive
                                    ? " text-blue-700 font-medium bg-blue-50/50"
                                    : "text-slate-600 hover:text-slate-900 hover:bg-slate-200/50"
                            }`}
                        >
                            {isActive && (
                                <div className="absolute left-0 top-0 w-1.5 h-full rounded-r-full bg-blue-600"></div>
                            )}
                            <Icon
                                className={`w-4 h-4 transition-transform duration-200 ${isActive ? "scale-110" : ""}`}
                                strokeWidth={isActive ? 2.2 : 1.8}
                            />
                            {item.label}
                        </button>
                    );
                })}
            </nav>
        </aside>
    );
}
