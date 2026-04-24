import { useMsal, useIsAuthenticated } from "@azure/msal-react";
import { useEffect, useState } from "react";
import { BACKEND_ORIGIN } from "../lib/api";
import { AUTH_SCOPES, APP_NAME } from "../lib/constants";
import { ShieldCheck, AlertCircle } from "lucide-react";

const MicrosoftLogo = () => (
    <svg
        xmlns="http://www.w3.org/2000/svg"
        width="20"
        height="20"
        viewBox="0 0 21 21"
    >
        <rect x="1" y="1" width="9" height="9" fill="#f25022" />
        <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
        <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
        <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
);

function getProgressLabel(progress: number): string {
    if (progress < 30) return "Authenticating";
    if (progress < 60) return "Exchanging tokens";
    if (progress < 90) return "Establishing session";
    if (progress < 100) return "Almost there";
    return "Done!";
}

// Module-scope auth state survives React StrictMode remounts
let runtimeSessionReady = false;
let pendingSessionExchange: Promise<boolean> | null = null;

export const AuthGate = ({ children }: { children: React.ReactNode }) => {
    const { instance, accounts } = useMsal();
    const isAuthenticated = useIsAuthenticated();
    const [hasInternalSession, setHasInternalSession] =
        useState(runtimeSessionReady);
    const [authError, setAuthError] = useState<string | null>(null);
    const [progress, setProgress] = useState(0);
    const [showApp, setShowApp] = useState(runtimeSessionReady);

    // Stepped progress bar
    useEffect(() => {
        if (!isAuthenticated || authError) return;

        if (hasInternalSession) {
            setProgress(100);
            const reveal = setTimeout(() => setShowApp(true), 600);
            return () => clearTimeout(reveal);
        }

        setProgress(0);
        const timer = setInterval(() => {
            setProgress((prev) => {
                if (prev >= 90) return prev;
                if (prev < 30) return prev + 8;
                if (prev < 60) return prev + 4;
                return prev + 1;
            });
        }, 300);

        return () => clearInterval(timer);
    }, [isAuthenticated, hasInternalSession, authError]);

    // Session exchange with backend
    useEffect(() => {
        if (!isAuthenticated) {
            runtimeSessionReady = false;
            pendingSessionExchange = null;
            setHasInternalSession(false);
            setAuthError(null);
            return;
        }

        if (runtimeSessionReady) {
            if (!hasInternalSession) setHasInternalSession(true);
            return;
        }

        if (!accounts[0] || hasInternalSession) return;

        const establishSession = async () => {
            try {
                if (!pendingSessionExchange) {
                    pendingSessionExchange = (async () => {
                        const response = await instance.acquireTokenSilent({
                            scopes: AUTH_SCOPES,
                            account: accounts[0],
                        });

                        const apiResponse = await fetch(
                            `${BACKEND_ORIGIN}/api/auth/session`,
                            {
                                method: "POST",
                                headers: {
                                    "Content-Type": "application/json",
                                },
                                body: JSON.stringify({
                                    id_token: response.idToken,
                                }),
                                credentials: "include",
                            },
                        );

                        return apiResponse.ok;
                    })().finally(() => {
                        pendingSessionExchange = null;
                    });
                }

                const success = await pendingSessionExchange;
                if (success) {
                    runtimeSessionReady = true;
                    setHasInternalSession(true);
                } else {
                    setAuthError(
                        "Failed to secure internal session. Please try again.",
                    );
                }
            } catch (error) {
                console.error("Session exchange failed", error);
                setAuthError("Authentication sync failed.");
            }
        };

        establishSession();
    }, [isAuthenticated, accounts, instance, hasInternalSession]);

    if (showApp) return <>{children}</>;

    // Single card shell for login, progress, and error — no layout jump between states
    return (
        <div className="flex h-screen flex-col items-center justify-center bg-gray-100">
            <div className="w-full max-w-md rounded-lg border border-slate-200 bg-white p-8 text-center">
                {!isAuthenticated ? (
                    <div key="login" className="space-y-6 animate-fade-in">
                        <div className="flex flex-col items-center gap-1">
                            <div className="flex items-center gap-2">
                                <span className="text-2xl font-extrabold tracking-tight text-red-600">
                                    MAQ
                                </span>
                                <span className="text-2xl font-medium tracking-tight text-slate-600">
                                    Software
                                </span>
                            </div>
                            <span className="text-sm font-semibold">
                                Energy Dashboard
                            </span>
                        </div>

                        <div className="border-t border-slate-100" />

                        <p className="text-sm text-slate-500">
                            Sign in with your organization account to continue
                        </p>

                        <button
                            onClick={() =>
                                instance.loginRedirect({
                                    scopes: AUTH_SCOPES,
                                })
                            }
                            className="w-full inline-flex items-center justify-center gap-2.5 rounded-md border border-slate-200 bg-white px-5 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50 hover:border-slate-300 transition-colors cursor-pointer"
                        >
                            <MicrosoftLogo />
                            Sign in with Microsoft
                        </button>

                        <p className="text-[11px] text-slate-400">
                            Protected by Microsoft Entra ID
                        </p>
                    </div>
                ) : authError ? (
                    <div
                        key="error"
                        className="flex flex-col items-center gap-3 py-4 animate-fade-in"
                    >
                        <div className="w-10 h-10 rounded-full bg-red-50 flex items-center justify-center">
                            <AlertCircle className="w-5 h-5 text-red-500" />
                        </div>
                        <p className="text-sm font-semibold text-red-600">
                            {authError}
                        </p>
                    </div>
                ) : (
                    <div
                        key="progress"
                        className="flex flex-col items-center gap-4 py-4 animate-fade-in"
                    >
                        <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
                            <ShieldCheck className="w-5 h-5 text-blue-600" />
                            Securing your session
                        </div>
                        <p className="text-[11px] text-slate-400">
                            {getProgressLabel(progress)}
                        </p>
                        <div className="w-full max-w-xs mx-auto h-1.5 bg-slate-100 rounded-full overflow-hidden">
                            <div
                                className="h-full bg-blue-600 rounded-full transition-all duration-300 ease-out"
                                style={{ width: `${progress}%` }}
                            />
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};
