import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MsalProvider } from "@azure/msal-react";

// 🚀 IMPORT your auth config and gate
import { msalInstance } from "./auth/msalConfig"; 
import { AuthGate } from "./components/AuthGate"; 

import "./index.css";
import App from "./App.jsx";

const queryClient = new QueryClient({
    defaultOptions: {
        queries: {
            staleTime: 5 * 60 * 1000,
            refetchOnWindowFocus: false,
        },
    },
});

// 🚀 INITIALIZE MSAL before rendering the app
msalInstance.initialize().then(() => {
    createRoot(document.getElementById("root")).render(
        <StrictMode>
            {/* 1. MsalProvider gives the whole app access to Microsoft login state */}
            <MsalProvider instance={msalInstance}>
                <QueryClientProvider client={queryClient}>
                    {/* 2. AuthGate blocks rendering until the secure cookie is set */}
                    <AuthGate>
                        {/* 3. Your completely unmodified App layout! */}
                        <App />
                    </AuthGate>
                </QueryClientProvider>
            </MsalProvider>
        </StrictMode>,
    );
});