import { Configuration, PublicClientApplication } from "@azure/msal-browser";

const env = import.meta.env as any;

// In production, window.location.origin is fine — but locally it resolves to
// http://localhost:5172, which IS registered in Azure, so it works either way.
// Being explicit here prevents surprises if the dev server port ever changes.
const redirectUri =
    env.VITE_REDIRECT_URI ||   // override via .env if needed
    window.location.origin;    // http://localhost:5172 locally, prod domain in prod

export const msalConfig: Configuration = {
    auth: {
        clientId: env.VITE_AZURE_CLIENT_ID || "",
        authority: `https://login.microsoftonline.com/${env.VITE_AZURE_TENANT_ID}`,
        redirectUri,
    },
    cache: {
        // sessionStorage is scoped to a single tab — safer than localStorage
        cacheLocation: "sessionStorage",
    }
};

export const msalInstance = new PublicClientApplication(msalConfig);