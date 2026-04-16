import { useMsal, useIsAuthenticated } from "@azure/msal-react";
import { useEffect, useState } from "react";

export const AuthGate = ({ children }: { children: React.ReactNode }) => {
    const { instance, accounts } = useMsal();
    const isAuthenticated = useIsAuthenticated();
    const [hasInternalSession, setHasInternalSession] = useState(false);
    const [authError, setAuthError] = useState<string | null>(null);

    useEffect(() => {
        const establishSession = async () => {
            if (isAuthenticated && accounts[0] && !hasInternalSession) {
                try {
                    // 1. Ask MSAL for the ID Token silently
                    const response = await instance.acquireTokenSilent({
                        scopes: ["User.Read"],
                        account: accounts[0]
                    });

                    // Locally: VITE_API_URL is unset, so this falls back to localhost:8000
                    const backendBase = import.meta.env.VITE_API_URL
                        ? `https://${import.meta.env.VITE_API_URL}`
                        : "http://localhost:8000";

                    // 2. Send ID token to FastAPI to exchange for the HttpOnly session cookie
                    const apiResponse = await fetch(`${backendBase}/api/auth/session`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ id_token: response.idToken }),
                        // ✅ MUST be "include" so the Set-Cookie header is accepted
                        //    and the cookie is sent on all subsequent API requests
                        credentials: "include",
                    });

                    if (apiResponse.ok) {
                        setHasInternalSession(true);
                    } else {
                        setAuthError("Failed to secure internal session. Please try again.");
                    }
                } catch (error) {
                    console.error("Session exchange failed", error);
                    setAuthError("Authentication sync failed.");
                }
            }
        };

        establishSession();
    }, [isAuthenticated, accounts, instance, hasInternalSession]);

    // UI: Not logged into Microsoft yet
    if (!isAuthenticated) {
        return (
            <div className="flex h-screen flex-col items-center justify-center bg-slate-50">
                <div className="p-8 bg-white rounded shadow-md text-center">
                    <h1 className="text-2xl font-bold mb-6">Energy Dashboard</h1>
                    <button 
                        onClick={() => instance.loginRedirect({ scopes: ["User.Read"] })}
                        className="bg-blue-600 hover:bg-blue-700 text-white font-semibold px-6 py-2 rounded transition-colors"
                    >
                        Sign in with Microsoft
                    </button>
                </div>
            </div>
        );
    }

    // UI: Logged into Microsoft, but waiting for FastAPI cookie
    if (!hasInternalSession) {
        return (
            <div className="flex h-screen items-center justify-center">
                {authError ? (
                    <div className="text-red-600 font-semibold">{authError}</div>
                ) : (
                    <div className="text-gray-600 animate-pulse">Securing session...</div>
                )}
            </div>
        );
    }

    // UI: Fully authenticated! Render the actual app.
    return <>{children}</>;
};