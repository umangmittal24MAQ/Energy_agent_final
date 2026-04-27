import { useState } from "react";
import Sidebar from "./components/Sidebar";
import Overview from "./pages/Overview";
import Scheduler from "./pages/Scheduler";

const PAGES = {
    overview: Overview,
    settings: Scheduler,
};

export default function App() {
    const [activePage, setActivePage] = useState("overview");

    const Page = PAGES[activePage];
    const mainOverflowClass = "overflow-y-auto";

    return (
        <div className="flex m-4 gap-4 h-[calc(100vh-2rem)]">
            <Sidebar active={activePage} onNavigate={setActivePage} />
            <main className={`flex-1 min-h-0 min-w-0 ${mainOverflowClass}`}>
                <div key={activePage} className="animate-fade-in">
                    <Page />
                </div>
            </main>
        </div>
    );
}
