import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MsalProvider } from "@azure/msal-react";

import { msalInstance } from "./auth/msalConfig";
import { AuthGate } from "./components/AuthGate";
import { QUERY_STALE_TIME_MS } from "./lib/constants";

import "./index.css";
import App from "./App.jsx";

const queryClient = new QueryClient({
    defaultOptions: {
        queries: {
            staleTime: QUERY_STALE_TIME_MS,
            refetchOnWindowFocus: false,
        },
    },
});

// Initialize MSAL before rendering
msalInstance
    .initialize()
    .then(() => msalInstance.handleRedirectPromise())
    .then(() => {
        createRoot(document.getElementById("root")).render(
            <StrictMode>
                <MsalProvider instance={msalInstance}>
                    <QueryClientProvider client={queryClient}>
                        <AuthGate>
                            <App />
                        </AuthGate>
                    </QueryClientProvider>
                </MsalProvider>
            </StrictMode>,
        );
    });
