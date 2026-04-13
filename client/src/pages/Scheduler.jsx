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

        if (status?.next_run) {
          setNextRunLabel(formatDateTime(status.next_run));
        }
        setIsSchedulerRunning(status?.status === "running");
      } catch {
        // Keep form defaults if scheduler config/status fetch fails.
      }
    }

    loadScheduler();
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
      start_time: startTime,
      subject,
      auto_start:
        autoStartOverride == null
          ? isSchedulerRunning
          : autoStartOverride,
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

  async function handleSaveConfiguration() {
    await persistConfiguration({ showSuccess: true });
  }

  async function handleSendNow() {
    const toList = recipients.filter(Boolean);
    if (toList.length === 0) return;

    setSending(true);
    setSuccessMsg(null);
    setErrorMsg(null);

    try {
      const saved = await persistConfiguration({
        showSuccess: false,
        autoStartOverride: isSchedulerRunning,
      });
      if (!saved) {
        return;
      }

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

  async function handleSchedule() {
    setScheduling(true);
    setSuccessMsg(null);
    setErrorMsg(null);

    try {
      const saved = await persistConfiguration({
        showSuccess: false,
        autoStartOverride: true,
      });
      if (!saved) {
        return;
      }

      const started = await startScheduler(startTime);
      if (started?.next_run) {
        setNextRunLabel(formatDateTime(started.next_run));
      }
      setIsSchedulerRunning(true);

      setSuccessMsg(`Daily slot checks started at ${startTime}.`);
      setTimeout(() => setSuccessMsg(null), 5000);
    } catch (err) {
      setErrorMsg(err.message || "Failed to schedule email");
      setTimeout(() => setErrorMsg(null), 5000);
    } finally {
      setScheduling(false);
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
    <div className="px-8 py-6 space-y-6 bg-gray-100 rounded-3xl h-[calc(100vh-2rem)] flex flex-col overflow-hidden">
      {/* Header */}
      <div className="shrink-0">
        <h1 className="text-xl font-semibold text-slate-900 flex items-center gap-2">
          <CalendarClock className="w-5 h-5 text-blue-600" />
          Scheduler
        </h1>
        <p className="text-xs text-slate-500 mt-0.5">
          Schedule or send energy report emails to recipients
        </p>
      </div>

      <div className="flex-1 h-full overflow-y-auto pr-1 space-y-6">
        <div className="grid grid-cols-2 h-full gap-4">
          {/* Email Configuration */}
          <div className="bg-white rounded-lg border border-slate-200 p-6 flex flex-col justify-between">
            <h2 className="text-sm font-semibold text-slate-800 flex items-center gap-2 pb-1">
              <Mail className="w-4 h-4 text-blue-600" />
              Email Configuration
            </h2>

            <div className="flex items-start justify-between gap-2">
              {/* Recipients */}
              <div className="space-y-2">
                <label className="flex items-center gap-1.5 text-xs font-medium text-slate-600 uppercase tracking-wide">
                  <Users className="w-3.5 h-3.5 text-slate-400" />
                  To
                </label>
                <div className="space-y-2">
                  {recipients.map((email, idx) => (
                    <div key={idx} className="flex items-center gap-2">
                      <input
                        type="email"
                        value={email}
                        onChange={(e) => updateRecipient(idx, e.target.value)}
                        placeholder="email@example.com"
                        className="flex-1 px-3 py-2 text-sm border border-slate-200 rounded-lg outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-100 transition-colors placeholder:text-slate-300"
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
                  className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700 font-medium cursor-pointer"
                >
                  <Plus className="w-3.5 h-3.5" />
                  Add recipient
                </button>
              </div>

              {/* CC */}
              {!showCc ? (
                <button
                  onClick={() => {
                    setShowCc(true);
                    if (cc.length === 0) setCc([""]);
                  }}
                  className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700 font-medium cursor-pointer"
                >
                  <Plus className="w-3.5 h-3.5" />
                  Add CC
                </button>
              ) : (
                <div className="space-y-2">
                  <label className="flex items-center gap-1.5 text-xs font-medium text-slate-600 uppercase tracking-wide">
                    <Mail className="w-3.5 h-3.5 text-slate-400" />
                    CC
                  </label>
                  <div className="space-y-2">
                    {cc.map((email, idx) => (
                      <div key={idx} className="flex items-center gap-2">
                        <input
                          type="email"
                          value={email}
                          onChange={(e) => updateCc(idx, e.target.value)}
                          placeholder="cc@example.com"
                          className="flex-1 px-3 py-2 text-sm border border-slate-200 rounded-lg outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-100 transition-colors placeholder:text-slate-300"
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
                    className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700 font-medium cursor-pointer"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    Add CC
                  </button>
                </div>
              )}
            </div>

            {/* Subject */}
            <div className="space-y-2">
              <label className="flex items-center gap-1.5 text-xs font-medium text-slate-600 uppercase tracking-wide">
                <FileText className="w-3.5 h-3.5 text-slate-400" />
                Subject
              </label>
              <input
                type="text"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-100 transition-colors placeholder:text-slate-300"
              />
            </div>

            {/* Schedule */}
            <div className="space-y-2">
              <label className="flex items-center gap-1.5 text-xs font-medium text-slate-600 uppercase tracking-wide">
                <Clock className="w-3.5 h-3.5 text-slate-400" />
                Start Time
              </label>
              <input
                type="time"
                value={startTime}
                onChange={(e) => setStartTime(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-100 transition-colors text-slate-700"
              />
            </div>
            {/* Controls */}
            <h2 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
              <CalendarClock className="w-4 h-4 text-blue-600" />
              Controls
            </h2>
            <div className="grid grid-cols-4 gap-2 [&>button]:p-2">
              <button
                onClick={handleSaveConfiguration}
                disabled={saving || scheduling || sending}
                className="flex items-center justify-center gap-2 w-full border border-blue-200 text-blue-700 text-sm font-medium rounded-lg hover:bg-blue-50 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Save className="w-4 h-4" />
                Save
              </button>
              <button
                onClick={handleSchedule}
                disabled={scheduling || saving}
                className="flex items-center justify-center gap-2 w-full bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors cursor-pointer"
              >
                <CalendarClock className="w-4 h-4" />
                Schedule Mail
              </button>
              <button
                onClick={handleStopScheduler}
                disabled={scheduling || saving}
                className="flex items-center justify-center gap-2 w-full border border-red-200 text-red-600 text-sm font-medium rounded-lg hover:bg-red-50 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <StopCircle className="w-4 h-4" />
                Stop Scheduler
              </button>
              <button
                onClick={handleSendNow}
                disabled={sending || saving || scheduling}
                className="flex items-center justify-center gap-2 w-full border border-slate-200 text-slate-700 text-sm font-medium rounded-lg hover:bg-slate-50 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {sending ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Send className="w-4 h-4" />
                )}
                {sending ? "Sending..." : "Send Now (Test)"}
              </button>
            </div>
            <p className="text-xs text-slate-400 mt-1">
              Next scheduled: {nextRunLabel}
            </p>
          </div>

          {/* Right Column: Controls + History */}
          <div className="space-y-6">
            {/* Success Toast */}
            {successMsg && (
              <div className="flex items-center gap-2.5 text-sm text-emerald-700 border border-emerald-200 bg-emerald-50 px-5 py-3 rounded-lg shrink-0 animate-fade-in">
                <CheckCircle2 className="w-4 h-4 shrink-0" />
                {successMsg}
                <button
                  onClick={() => setSuccessMsg(null)}
                  className="ml-auto p-0.5 text-emerald-400 hover:text-emerald-600 cursor-pointer"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            )}
            {/* Error Toast */}
            {errorMsg && (
              <div className="flex items-center gap-2.5 text-sm text-red-600 border border-red-200 bg-red-50 px-5 py-3 rounded-lg shrink-0 animate-fade-in">
                <AlertCircle className="w-4 h-4 shrink-0" />
                {errorMsg}
                <button
                  onClick={() => setErrorMsg(null)}
                  className="ml-auto p-0.5 text-red-400 hover:text-red-600 cursor-pointer"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            )}
            {/* Send History */}
            <div className="bg-white rounded-lg border border-slate-200">
              <div className="px-6 py-4 border-b border-slate-100 flex items-center gap-2">
                <History className="w-4 h-4 text-blue-600" />
                <h2 className="text-sm font-semibold text-slate-800">
                  Send History
                </h2>
              </div>
              {sendHistory.length === 0 ? (
                <p className="px-6 py-4 text-xs text-slate-400">
                  No emails sent yet this session.
                </p>
              ) : (
                <div className="divide-y divide-slate-100">
                  {sendHistory.map((entry) => (
                    <div
                      key={entry.id}
                      className="px-6 py-3 flex items-center justify-between"
                    >
                      <div className="min-w-0">
                        <p className="text-sm text-slate-700 truncate">
                          {entry.subject}
                        </p>
                        <p className="text-xs text-slate-400 mt-0.5">
                          {entry.to} · {formatDateTime(entry.sentAt)}
                        </p>
                      </div>
                      <span
                        className={`text-xs font-medium px-2 py-0.5 rounded-full shrink-0 ml-3 ${
                          entry.status === "delivered"
                            ? "bg-emerald-50 text-emerald-600"
                            : "bg-red-50 text-red-500"
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
