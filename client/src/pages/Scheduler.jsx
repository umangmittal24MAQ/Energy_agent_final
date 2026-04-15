import { useState, useMemo, useEffect } from "react";
import {
  sendTestEmail,
  fetchSchedulerConfig,
  updateSchedulerConfig,
  fetchSchedulerStatus,
  startScheduler,
  stopSchedulerApi,
} from "../lib/api";
import {
  CalendarClock,
  Send,
  Save,
  Plus,
  X,
  Clock,
  Users,
  Mail,
  StopCircle,
  CheckCircle2,
  History,
  FileText,
  Loader2,
  AlertCircle,
} from "lucide-react";

function getDefaultTime() {
  return "09:00";
}

function formatDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function Scheduler() {
  const today = useMemo(
    () =>
      new Date().toLocaleDateString("en-IN", {
        day: "2-digit",
        month: "short",
        year: "numeric",
      }),
    [],
  );

  const [recipients, setRecipients] = useState(["ishitas@maqsoftware.com"]);
  const [cc, setCc] = useState(["@maqsoftware.com"]);
  const [subject, setSubject] = useState(
    `Energy Report — Daily Summary (${today})`,
  );
  const [startTime, setStartTime] = useState(getDefaultTime);
  const [nextRunLabel, setNextRunLabel] = useState("—");
  const [showCc, setShowCc] = useState(true);
  const [successMsg, setSuccessMsg] = useState(null);
  const [errorMsg, setErrorMsg] = useState(null);
  const [sending, setSending] = useState(false);
  const [saving, setSaving] = useState(false);
  const [scheduling, setScheduling] = useState(false);
  const [sendHistory, setSendHistory] = useState([]);
  const [isSchedulerRunning, setIsSchedulerRunning] = useState(false);

  // Memoized lists for clean UI display at the top
  const toRecipients = useMemo(
    () => recipients.map((r) => r.trim()).filter(Boolean),
    [recipients],
  );
  const ccRecipients = useMemo(
    () => cc.map((r) => r.trim()).filter(Boolean),
    [cc],
  );

  useEffect(() => {
    async function loadScheduler() {
      try {
        const [config, status] = await Promise.all([
          fetchSchedulerConfig(),
          fetchSchedulerStatus(),
        ]);

        if (config?.to) {
          const toList = String(config.to)
            .split(",")
            .map((v) => v.trim())
            .filter(Boolean);
          if (toList.length) setRecipients(toList);
        }

        if (config?.cc != null) {
          const ccList = String(config.cc)
            .split(",")
            .map((v) => v.trim())
            .filter(Boolean);
          setCc(ccList.length ? ccList : [""]);
          setShowCc(ccList.length > 0);
        }

        if (config?.subject) setSubject(config.subject);
        if (config?.start_time) {
          setStartTime(config.start_time);
        } else if (config?.send_time) {
          setStartTime(config.send_time);
        }

        setNextRunLabel(
          status?.next_run ? formatDateTime(status.next_run) : "—",
        );
        setIsSchedulerRunning(status?.status === "running");
      } catch {
        // Keep form defaults if scheduler config/status fetch fails.
      }
    }

    loadScheduler();
  }, []);

  // Polling to keep the Next Run time updated live
  useEffect(() => {
    const intervalId = setInterval(async () => {
      try {
        const status = await fetchSchedulerStatus();
        setIsSchedulerRunning(status?.status === "running");
        setNextRunLabel(
          status?.next_run ? formatDateTime(status.next_run) : "—",
        );
      } catch {
        // Keep current status display if polling fails temporarily.
      }
    }, 30000);

    return () => clearInterval(intervalId);
  }, []);

  function addRecipient() {
    setRecipients((prev) => [...prev, ""]);
  }
  function removeRecipient(idx) {
    setRecipients((prev) => prev.filter((_, i) => i !== idx));
  }
  function updateRecipient(idx, value) {
    setRecipients((prev) => prev.map((v, i) => (i === idx ? value : v)));
  }
  function addCc() {
    setCc((prev) => [...prev, ""]);
  }
  function removeCc(idx) {
    setCc((prev) => prev.filter((_, i) => i !== idx));
  }
  function updateCc(idx, value) {
    setCc((prev) => prev.map((v, i) => (i === idx ? value : v)));
  }

  function buildConfigPayload(autoStartOverride = null) {
    return {
      to: recipients.filter(Boolean).join(","),
      cc: cc.filter(Boolean).join(","),
      send_time: startTime,
      subject,
      auto_start:
        autoStartOverride == null ? isSchedulerRunning : autoStartOverride,
    };
  }

  async function persistConfiguration(options = {}) {
    const { showSuccess = true, autoStartOverride = null } = options;
    const toList = recipients.filter(Boolean);
    if (toList.length === 0) {
      setErrorMsg("Please add at least one recipient.");
      setTimeout(() => setErrorMsg(null), 5000);
      return false;
    }

    setSaving(true);
    setSuccessMsg(null);
    setErrorMsg(null);

    try {
      await updateSchedulerConfig(buildConfigPayload(autoStartOverride));
      if (autoStartOverride != null) {
        setIsSchedulerRunning(Boolean(autoStartOverride));
      }
      if (showSuccess) {
        setSuccessMsg("Configuration saved successfully.");
        setTimeout(() => setSuccessMsg(null), 5000);
      }
      return true;
    } catch (err) {
      setErrorMsg(err.message || "Failed to save configuration");
      setTimeout(() => setErrorMsg(null), 5000);
      return false;
    } finally {
      setSaving(false);
    }
  }

  // SAVE: Forces auto_start to TRUE
  async function handleSaveConfiguration() {
    const saved = await persistConfiguration({ showSuccess: true, autoStartOverride: true });
    if (saved) {
      // Refresh the clock to show the new Next Run time
      const status = await fetchSchedulerStatus();
      setIsSchedulerRunning(status?.status === "running");
      setNextRunLabel(status?.next_run ? formatDateTime(status.next_run) : "—");
    }
  }

  // SEND NOW: Forces auto_start to FALSE
  async function handleSendNow() {
    const toList = recipients.filter(Boolean);
    if (toList.length === 0) return;

    setSending(true);
    setSuccessMsg(null);
    setErrorMsg(null);

    try {
      const saved = await persistConfiguration({
        showSuccess: false,
        autoStartOverride: false, // STOPS the clock
      });
      if (!saved) {
        return;
      }
      
      setNextRunLabel("—"); // Clear the clock label immediately

      await sendTestEmail();

      setSendHistory((prev) => [
        {
          id: Date.now(),
          to: toList[0],
          subject,
          sentAt: new Date().toISOString(),
          status: "delivered",
        },
        ...prev,
      ]);
      setSuccessMsg("Email sent successfully to " + toList.join(", "));
      setTimeout(() => setSuccessMsg(null), 5000);
    } catch (err) {
      setSendHistory((prev) => [
        {
          id: Date.now(),
          to: toList[0],
          subject,
          sentAt: new Date().toISOString(),
          status: "failed",
        },
        ...prev,
      ]);
      setErrorMsg(err.message || "Failed to send email");
      setTimeout(() => setErrorMsg(null), 5000);
    } finally {
      setSending(false);
    }
  }

  async function handleStopScheduler() {
    setScheduling(true);
    setSuccessMsg(null);
    setErrorMsg(null);

    try {
      const stopped = await stopSchedulerApi();
      if (!stopped?.next_run) setNextRunLabel("—");
      setIsSchedulerRunning(false);
      setSuccessMsg("Scheduler stopped successfully.");
      setTimeout(() => setSuccessMsg(null), 5000);
    } catch (err) {
      setErrorMsg(err.message || "Failed to stop scheduler");
      setTimeout(() => setErrorMsg(null), 5000);
    } finally {
      setScheduling(false);
    }
  }

  return (
    <div className="h-[calc(100vh-2rem)] rounded-2xl border border-slate-200 bg-slate-100 p-4 flex flex-col overflow-hidden">
      
      {/* --- TOP SUMMARY HEADER --- */}
      <div className="shrink-0 rounded-xl border border-slate-200 bg-white px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-slate-900 flex items-center gap-2">
              <CalendarClock className="w-5 h-5 text-blue-600" />
              Scheduler Dashboard
            </h1>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium border ${
                isSchedulerRunning
                  ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                  : "border-slate-200 bg-slate-50 text-slate-600"
              }`}
            >
              {isSchedulerRunning ? (
                <CheckCircle2 className="w-3.5 h-3.5" />
              ) : (
                <StopCircle className="w-3.5 h-3.5" />
              )}
              {isSchedulerRunning ? "Running" : "Stopped"}
            </span>
          </div>
        </div>

        {/* Dynamic Data Display */}
        <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-4 border-t border-slate-100 pt-4">
          <div>
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1 flex items-center gap-1.5">
              <Clock className="w-3.5 h-3.5" /> Next Scheduled Run
            </p>
            <p className="text-sm font-medium text-slate-800">
              {isSchedulerRunning ? nextRunLabel : "Currently Stopped"}
            </p>
          </div>
          <div className="min-w-0">
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1 flex items-center gap-1.5">
              <Users className="w-3.5 h-3.5" /> Primary Recipients (To)
            </p>
            <p className="text-sm font-medium text-slate-800 truncate" title={toRecipients.join(", ")}>
              {toRecipients.length ? toRecipients.join(", ") : "None configured"}
            </p>
          </div>
          <div className="min-w-0">
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1 flex items-center gap-1.5">
              <Mail className="w-3.5 h-3.5" /> Carbon Copy (CC)
            </p>
            <p className="text-sm font-medium text-slate-800 truncate" title={ccRecipients.join(", ")}>
              {ccRecipients.length ? ccRecipients.join(", ") : "None configured"}
            </p>
          </div>
        </div>
      </div>

      <div className="mt-4 flex-1 overflow-hidden">
        <div className="grid h-full grid-cols-1 gap-4 xl:grid-cols-12">
          
          {/* --- LEFT PANEL: CONFIGURATION --- */}
          <div className="xl:col-span-8 min-h-0">
            <div className="h-full rounded-xl border border-slate-200 bg-white p-4 overflow-y-auto space-y-4">
              <div className="flex items-center justify-between border-b border-slate-100 pb-2">
                <h2 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
                  <Mail className="w-4 h-4 text-blue-600" />
                  Email Configuration
                </h2>
                <span className="text-xs text-slate-400">
                  Operational profile
                </span>
              </div>

              <div className="grid gap-3 lg:grid-cols-2">
                <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3 space-y-2">
                  <label className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-600 uppercase tracking-wide">
                    <Users className="w-3.5 h-3.5 text-slate-400" />
                    Recipients (To)
                  </label>
                  <div className="space-y-2">
                    {recipients.map((email, idx) => (
                      <div key={idx} className="flex items-center gap-2">
                        <input
                          type="email"
                          value={email}
                          onChange={(e) => updateRecipient(idx, e.target.value)}
                          placeholder="email@example.com"
                          className="w-full px-3 py-2 text-sm border border-slate-200 rounded-md outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 transition-colors placeholder:text-slate-300 bg-white"
                        />
                        {recipients.length > 1 && (
                          <button
                            onClick={() => removeRecipient(idx)}
                            className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-md transition-colors cursor-pointer"
                          >
                            <X className="w-4 h-4" />
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                  <button
                    onClick={addRecipient}
                    className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700 font-medium cursor-pointer"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    Add recipient
                  </button>
                </div>

                <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <label className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-600 uppercase tracking-wide">
                      <Mail className="w-3.5 h-3.5 text-slate-400" />
                      CC
                    </label>
                    {!showCc && (
                      <button
                        onClick={() => {
                          setShowCc(true);
                          if (cc.length === 0) setCc([""]);
                        }}
                        className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700 font-medium cursor-pointer"
                      >
                        <Plus className="w-3.5 h-3.5" />
                        Add CC
                      </button>
                    )}
                  </div>

                  {showCc ? (
                    <>
                      <div className="space-y-2">
                        {cc.map((email, idx) => (
                          <div key={idx} className="flex items-center gap-2">
                            <input
                              type="email"
                              value={email}
                              onChange={(e) => updateCc(idx, e.target.value)}
                              placeholder="cc@example.com"
                              className="w-full px-3 py-2 text-sm border border-slate-200 rounded-md outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 transition-colors placeholder:text-slate-300 bg-white"
                            />
                            <button
                              onClick={() =>
                                cc.length > 1 ? removeCc(idx) : setShowCc(false)
                              }
                              className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-md transition-colors cursor-pointer"
                            >
                              <X className="w-4 h-4" />
                            </button>
                          </div>
                        ))}
                      </div>
                      <button
                        onClick={addCc}
                        className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700 font-medium cursor-pointer"
                      >
                        <Plus className="w-3.5 h-3.5" />
                        Add CC
                      </button>
                    </>
                  ) : (
                    <p className="text-xs text-slate-400">
                      No CC recipients configured.
                    </p>
                  )}
                </div>
              </div>

              <div className="grid gap-3 lg:grid-cols-3">
                <div className="lg:col-span-2 space-y-2">
                  <label className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-600 uppercase tracking-wide">
                    <FileText className="w-3.5 h-3.5 text-slate-400" />
                    Subject
                  </label>
                  <input
                    type="text"
                    value={subject}
                    onChange={(e) => setSubject(e.target.value)}
                    className="w-full px-3 py-2 text-sm border border-slate-200 rounded-md outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 transition-colors placeholder:text-slate-300"
                  />
                </div>

                <div className="space-y-2">
                  <label className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-600 uppercase tracking-wide">
                    <Clock className="w-3.5 h-3.5 text-slate-400" />
                    Start Time
                  </label>
                  <input
                    type="time"
                    value={startTime}
                    onChange={(e) => setStartTime(e.target.value)}
                    className="w-full px-3 py-2 text-sm border border-slate-200 rounded-md outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 transition-colors text-slate-700"
                  />
                </div>
              </div>

              {/* --- ACTION BUTTONS --- */}
              <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3 mt-4">
                <div className="flex items-center justify-between gap-2 mb-3">
                  <h2 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
                    <CalendarClock className="w-4 h-4 text-blue-600" />
                    Execution Controls
                  </h2>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 [&>button]:px-3 [&>button]:py-2">
                  <button
                    onClick={handleSaveConfiguration}
                    disabled={saving || scheduling || sending}
                    className="inline-flex items-center justify-center gap-2 w-full bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <Save className="w-4 h-4" />
                    {saving ? "Saving..." : "Save & Schedule"}
                  </button>
                  <button
                    onClick={handleStopScheduler}
                    disabled={scheduling || saving || sending}
                    className="inline-flex items-center justify-center gap-2 w-full border border-red-200 text-red-600 text-sm font-medium rounded-md hover:bg-red-50 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <StopCircle className="w-4 h-4" />
                    Stop Clock
                  </button>
                  <button
                    onClick={handleSendNow}
                    disabled={sending || saving || scheduling}
                    className="inline-flex items-center justify-center gap-2 w-full border border-slate-300 text-slate-700 text-sm font-medium rounded-md hover:bg-slate-100 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed shadow-sm"
                  >
                    {sending ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Send className="w-4 h-4" />
                    )}
                    {sending ? "Sending..." : "Send Test Now"}
                  </button>
                </div>
                <p className="text-[11px] text-slate-500 mt-3 text-center">
                  * <span className="font-medium text-slate-600">Save & Schedule</span> arms the system automatically. <span className="font-medium text-slate-600">Send Test Now</span> will temporarily stop the background clock.
                </p>
              </div>
            </div>
          </div>

          {/* --- RIGHT PANEL: HISTORY & STATUS --- */}
          <div className="xl:col-span-4 min-h-0 flex flex-col gap-3">
            {successMsg && (
              <div className="flex items-center gap-2 text-sm text-emerald-700 border border-emerald-200 bg-emerald-50 px-3 py-2 rounded-lg animate-fade-in shrink-0">
                <CheckCircle2 className="w-4 h-4 shrink-0" />
                <span className="min-w-0 flex-1 truncate">{successMsg}</span>
                <button
                  onClick={() => setSuccessMsg(null)}
                  className="p-0.5 text-emerald-400 hover:text-emerald-600 cursor-pointer"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            )}

            {errorMsg && (
              <div className="flex items-center gap-2 text-sm text-red-600 border border-red-200 bg-red-50 px-3 py-2 rounded-lg animate-fade-in shrink-0">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span className="min-w-0 flex-1 truncate">{errorMsg}</span>
                <button
                  onClick={() => setErrorMsg(null)}
                  className="p-0.5 text-red-400 hover:text-red-600 cursor-pointer"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            )}

            <div className="rounded-xl border border-slate-200 bg-white p-3 shrink-0">
              <h3 className="text-xs font-semibold tracking-wide uppercase text-slate-500 mb-2">
                Activity Snapshot
              </h3>
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-md border border-slate-200 bg-slate-50 px-2.5 py-2">
                  <p className="text-[11px] text-slate-500">Total Sends</p>
                  <p className="text-sm font-semibold text-slate-800">
                    {sendHistory.length}
                  </p>
                </div>
                <div className="rounded-md border border-slate-200 bg-slate-50 px-2.5 py-2">
                  <p className="text-[11px] text-slate-500">Configured Time</p>
                  <p className="text-sm font-semibold text-slate-800">
                    {startTime}
                  </p>
                </div>
              </div>
            </div>

            <div className="flex-1 min-h-64 rounded-xl border border-slate-200 bg-white overflow-hidden flex flex-col">
              <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-2 shrink-0">
                <History className="w-4 h-4 text-blue-600" />
                <h2 className="text-sm font-semibold text-slate-800">
                  Send History
                </h2>
              </div>

              {sendHistory.length === 0 ? (
                <div className="flex-1 flex items-center justify-center p-4">
                  <p className="text-xs text-slate-400 text-center">
                    No emails sent yet in this session.
                  </p>
                </div>
              ) : (
                <div className="divide-y divide-slate-100 overflow-y-auto">
                  {sendHistory.map((entry) => (
                    <div
                      key={entry.id}
                      className="px-4 py-3 flex items-center justify-between gap-3"
                    >
                      <div className="min-w-0">
                        <p className="text-sm text-slate-700 truncate font-medium">
                          {entry.subject}
                        </p>
                        <p className="text-[11px] text-slate-400 mt-0.5 truncate">
                          {entry.to} · {formatDateTime(entry.sentAt)}
                        </p>
                      </div>
                      <span
                        className={`text-[10px] uppercase font-bold tracking-wide px-2 py-1 rounded-full shrink-0 ${
                          entry.status === "delivered"
                            ? "bg-emerald-50 text-emerald-600 border border-emerald-100"
                            : "bg-red-50 text-red-500 border border-red-100"
                        }`}
                      >
                        {entry.status}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}