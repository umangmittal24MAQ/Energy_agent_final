import { useState } from "react";
import Sidebar from "./components/Sidebar";
import Overview from "./pages/Overview";
import Solar from "./pages/Solar";
import Grid from "./pages/Grid";
import Diesel from "./pages/Diesel";
import Scheduler from "./pages/Scheduler";

const PAGES = {
  overview: Overview,
  solar: Solar,
  grid: Grid,
  diesel: Diesel,
  scheduler: Scheduler,
};

export default function App() {
  const [activePage, setActivePage] = useState("overview");

  const Page = PAGES[activePage];
  const mainOverflowClass =
    activePage === "overview" ? "overflow-y-auto" : "overflow-hidden";

  return (
    <div className="flex m-4 gap-4 h-[calc(100vh-2rem)]">
      <Sidebar active={activePage} onNavigate={setActivePage} />
      <main className={`flex-1 min-h-0 min-w-0 ${mainOverflowClass}`}>
        <div key={activePage} className="animate-fade-in h-full">
          <Page />
        </div>
      </main>
    </div>
  );
}
