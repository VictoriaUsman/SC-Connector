import { BrowserRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/sonner";
import { ThemeProvider } from "@/components/theme-provider";
import { Layout } from "@/components/layout";
import { Dashboard } from "@/pages/dashboard";
import { Clients } from "@/pages/clients";
import { Schedules } from "@/pages/schedules";
import { OnDemand } from "@/pages/on-demand";
import { Admin } from "@/pages/admin";
import { SlackBots } from "@/pages/slack-bots";
import { OAuthComplete } from "@/pages/oauth-complete";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

export function App() {
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route path="oauth-complete" element={<OAuthComplete />} />
            <Route element={<Layout />}>
              <Route index element={<Dashboard />} />
              <Route path="clients" element={<Clients />} />
              <Route path="schedules" element={<Schedules />} />
              <Route path="on-demand" element={<OnDemand />} />
              <Route path="slack-bots" element={<SlackBots />} />
              <Route path="admin" element={<Admin />} />
            </Route>
          </Routes>
        </BrowserRouter>
        <Toaster />
      </QueryClientProvider>
    </ThemeProvider>
  );
}
