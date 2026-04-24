import { LayoutDashboard, Settings, LogOut } from "lucide-react";
import { useMsal } from "@azure/msal-react";
import { BACKEND_ORIGIN } from "../lib/api";
import { APP_NAME } from "../lib/constants";

const NAV_ITEMS = [
    { key: "overview", label: "Overview", icon: LayoutDashboard },
];

export default function Sidebar({ active, onNavigate }) {
    const { instance } = useMsal();

    const handleLogout = async () => {
        try {
            const backendUrl = `${BACKEND_ORIGIN}/api/auth/logout`;

            // 1. Tell FastAPI to destroy the cookie
            await fetch(backendUrl, {
                method: "DELETE",
                credentials: "include",
            });

            // 2. Tell Microsoft to sign out locally
            instance.logoutRedirect({
                postLogoutRedirectUri: window.location.origin,
            });
        } catch (error) {
            console.error("Logout failed", error);
        }
    };

    return (
        <aside className="w-48 shrink-0 bg-gray-100 flex flex-col rounded-3xl sticky top-4 h-[calc(100vh-2rem)] self-start animate-fade-in">
            <div className="px-4 py-4 border-b border-slate-200">
                <div className="flex items-baseline gap-1 leading-none">
                    <span className="text-2xl font-extrabold tracking-tight text-red-600">
                        MAQ
                    </span>
                    <span className="text-2xl font-medium tracking-tight text-slate-600">
                        Software
                    </span>
                </div>
                <p className="mt-2 text-xs font-semibold text-slate-500 tracking-tight">
                    {APP_NAME}
                </p>
            </div>
            <span className="px-4 py-2 text-xs font-semibold text-slate-500 uppercase tracking-wider flex">
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
                            className={`w-full relative flex items-center gap-2 px-4 py-2 cursor-pointer transition-all duration-200 ${
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

            <div className="mt-auto px-2 pb-4 space-y-2">
                <button
                    onClick={() => onNavigate("settings")}
                    className={`w-full relative flex items-center gap-2 px-4 py-2 rounded-xl cursor-pointer transition-all duration-200 ${
                        active === "settings"
                            ? "text-blue-700 font-medium bg-blue-50"
                            : "text-slate-600 hover:text-slate-900 hover:bg-slate-200/50"
                    }`}
                >
                    <Settings className="w-4 h-4 transition-transform duration-200" />
                    Settings
                </button>
                <button
                    onClick={handleLogout}
                    className="w-full flex items-center gap-2.5 px-5 py-2.5 text-sm font-medium text-red-600 hover:bg-red-50 hover:text-red-700 rounded-xl transition-colors cursor-pointer"
                >
                    <LogOut className="w-4 h-4" />
                    Sign Out
                </button>
            </div>
        </aside>
    );
}
