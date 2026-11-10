import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { adoptTokenFromUrl, ApiError, BASE } from "./api/client";
import App from "./App";
import "./styles.css";

adoptTokenFromUrl();

// Under the ledger site the whole site shares one navigation bar (the ledger's sitenav.js); on its own, this app keeps its sidebar.
if (BASE) {
  const nav = document.createElement("script");
  nav.src = "/assets/sitenav.js?v=" + Math.floor(Date.now() / 3600000);   // a new URL each hour, so a stale copy is never kept
  nav.defer = true;
  document.head.appendChild(nav);
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
      // Retry only failures that might be temporary: network errors and 5xx, never 4xx.
      retry: (count, err) => count < 2 && (!(err instanceof ApiError) || err.status === 0 || err.status >= 500),
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={BASE || undefined}>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
