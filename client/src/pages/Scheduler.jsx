import { useState, useMemo, useEffect } from "react";
import {
  sendTestEmail,
  fetchSchedulerConfig,
  updateSchedulerConfig,
  fetchSchedulerStatus,
  fetchSchedulerHistory,
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
  Pencil,
  Settings,
} from "lucide-react";
import { useMsal } from "@azure/msal-react";
import { SCHEDULER, DATE_LOCALE } from "../lib/constants";

const RECIPIENTS_FALLBACK_KEY = "scheduler-recipients-fallback";
const CC_FALLBACK_KEY = "scheduler-cc-fallback";
const AUTHORIZED_ADMINS = [
  "umang.mittal@maqsoftware.com",
  "prajwal.khadse@maqsoftware.com",
];

function getDefaultTime() {
  return "09:00";
}

function formatDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(DATE_LOCALE, {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function toDate(value) {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

function isSendEvent(entry) {
  if (!entry || typeof entry !== "object") return false;
  const status = String(entry.status || "").toLowerCase();
  if (status !== "success" && status !== "failed") return false;
  return Boolean(
    entry.subject || entry.recipients || entry.kind === "daily_report",
  );
}

function isManualTrigger(entry) {
  const source = String(entry?.trigger_source || "").toLowerCase();
  return source === "api_manual" || source.includes("manual");
}

function getTriggerLabel(entry) {
  const source = String(entry?.trigger_source || "").toLowerCase();
  if (!source) return "Unknown";
  if (isManualTrigger(entry)) return "Manual";
  return "Scheduled";
}

function computeNextSchedulerSlot(
  startTimeValue,
  intervalMinutes = SCHEDULER.DEFAULT_INTERVAL_MINUTES,
  nowDate = new Date(),
) {
  const match = String(startTimeValue || "").match(/^(\d{1,2}):(\d{2})$/);
  if (!match) return null;

  const startHour = Number(match[1]);
  const startMinute = Number(match[2]);
  const safeInterval =
    Number.isFinite(intervalMinutes) && intervalMinutes > 0
      ? intervalMinutes
      : SCHEDULER.DEFAULT_INTERVAL_MINUTES;

  const baseToday = new Date(nowDate);
  baseToday.setHours(startHour, startMinute, 0, 0);

  const candidates = [];
  for (let dayOffset = 0; dayOffset <= 1; dayOffset += 1) {
    const dayBase = new Date(baseToday);
    dayBase.setDate(baseToday.getDate() + dayOffset);

    for (let i = 0; i < SCHEDULER.SLOTS_PER_DAY; i += 1) {
      candidates.push(
        new Date(dayBase.getTime() + i * safeInterval * 60 * 1000),
      );
    }
  }

  return candidates.find((slot) => slot.getTime() > nowDate.getTime()) || null;
}

function isValidEmail(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value || "").trim());
}

export default function Scheduler({
  settingsOnly = false,
  asSettings = false,
}) {
  const { accounts } = useMsal();
  const currentUserEmail = accounts?.[0]?.username?.toLowerCase() || "";
  const isAdmin = AUTHORIZED_ADMINS.includes(currentUserEmail);

  const today = useMemo(
    () =>
      new Date().toLocaleDateString(DATE_LOCALE, {
        year: "numeric",
        month: "long",
        day: "numeric",
      }),
    [],
  );

  const [recipients, setRecipients] = useState([]);
  const [cc, setCc] = useState([]);
  const [recipientInput, setRecipientInput] = useState("");
  const [ccInput, setCcInput] = useState("");
  const [recipientError, setRecipientError] = useState(null);
  const [ccError, setCcError] = useState(null);
  const [editingRecipientIndex, setEditingRecipientIndex] = useState(-1);
  const [editingRecipientValue, setEditingRecipientValue] = useState("");

  const [subject, setSubject] = useState(
    `Energy Report — Daily Summary (${today})`,
  );
  const [startTime, setStartTime] = useState(getDefaultTime);
  const [nextRunLabel, setNextRunLabel] = useState("—");
  const [successMsg, setSuccessMsg] = useState(null);
  const [errorMsg, setErrorMsg] = useState(null);
  const [sending, setSending] = useState(false);
  const [saving, setSaving] = useState(false);
  const [scheduling, setScheduling] = useState(false);
  const [sendHistory, setSendHistory] = useState([]);
  const [isSchedulerRunning, setIsSchedulerRunning] = useState(true);
  const [configuredIntervalMinutes, setConfiguredIntervalMinutes] = useState(
    SCHEDULER.DEFAULT_INTERVAL_MINUTES,
  );
  const [nowTick, setNowTick] = useState(() => Date.now());

  const toRecipients = useMemo(
    () => recipients.map((r) => r.trim()).filter(Boolean),
    [recipients],
  );
  const ccRecipients = useMemo(
    () => cc.map((r) => r.trim()).filter(Boolean),
    [cc],
  );

  const nextScheduledMailLabel = useMemo(() => {
    const slot = computeNextSchedulerSlot(
      startTime,
      configuredIntervalMinutes,
      new Date(nowTick),
    );
    if (!slot) return "—";
    return formatDateTime(slot.toISOString());
  }, [startTime, configuredIntervalMinutes, nowTick]);

  const totalSendsCount = useMemo(
    () => sendHistory.filter(isSendEvent).length,
    [sendHistory],
  );

  const displayedSendHistory = useMemo(() => {
    const sendEvents = sendHistory.filter(isSendEvent);
    const scheduledEvents = sendEvents.filter(
      (entry) => !isManualTrigger(entry),
    );
    const manualEvents = sendEvents.filter((entry) => isManualTrigger(entry));

    const mixed = [
      ...scheduledEvents.slice(0, 10),
      ...manualEvents.slice(0, 10),
    ]
      .sort((a, b) => {
        const aTs = toDate(a?.timestamp)?.getTime() ?? 0;
        const bTs = toDate(b?.timestamp)?.getTime() ?? 0;
        return bTs - aTs;
      })
      .slice(0, Math.max(SCHEDULER.DISPLAY_HISTORY_COUNT, 10));

    return mixed;
  }, [sendHistory]);

  async function refreshSchedulerStatus() {
    const status = await fetchSchedulerStatus();
    const running = String(status?.status || "").toLowerCase() === "running";
    setIsSchedulerRunning(running);
    setNextRunLabel(status?.next_run ? formatDateTime(status.next_run) : "—");
    return status;
  }

  async function refreshSchedulerHistory() {
    const historyPayload = await fetchSchedulerHistory();
    const entries = Array.isArray(historyPayload?.entries)
      ? historyPayload.entries
      : [];

    const sortedEntries = [...entries].sort((a, b) => {
      const aTs = toDate(a?.timestamp)?.getTime() ?? 0;
      const bTs = toDate(b?.timestamp)?.getTime() ?? 0;
      return bTs - aTs;
    });

    setSendHistory(sortedEntries);
    return sortedEntries;
  }

  async function waitForSendHistoryUpdate(previousTopTimestamp) {
    for (
      let attempt = 0;
      attempt < SCHEDULER.MAX_RETRY_ATTEMPTS;
      attempt += 1
    ) {
      const latestEntries = await refreshSchedulerHistory();
      const latestTimestamp = latestEntries?.[0]?.timestamp || null;
      if (latestTimestamp && latestTimestamp !== previousTopTimestamp) {
        return;
      }
      await new Promise((resolve) =>
        setTimeout(resolve, SCHEDULER.RETRY_DELAY_MS),
      );
    }
  }

  function applyFallbackRecipients() {
    const fallbackTo = localStorage.getItem(RECIPIENTS_FALLBACK_KEY);
    const fallbackCc = localStorage.getItem(CC_FALLBACK_KEY);

    if (fallbackTo) {
      const toList = fallbackTo
        .split(",")
        .map((v) => v.trim())
        .filter(Boolean);
      if (toList.length) setRecipients(toList);
    }

    if (fallbackCc) {
      const ccList = fallbackCc
        .split(",")
        .map((v) => v.trim())
        .filter(Boolean);
      setCc(ccList);
    }
  }

  useEffect(() => {
    async function loadScheduler() {
      try {
        const [config, status] = await Promise.all([
          fetchSchedulerConfig(),
          refreshSchedulerStatus(),
          settingsOnly ? Promise.resolve(null) : refreshSchedulerHistory(),
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
          setCc(ccList);
        }

        if (config?.subject) setSubject(config.subject);
        if (config?.start_time) {
          setStartTime(config.start_time);
        } else if (config?.send_time) {
          setStartTime(config.send_time);
        }

        const explicitInterval = Number(config?.reminder_interval_minutes);
        if (Number.isFinite(explicitInterval) && explicitInterval > 0) {
          setConfiguredIntervalMinutes(explicitInterval);
        }

        setIsSchedulerRunning(
          String(status?.status || "").toLowerCase() === "running",
        );
        setNowTick(Date.now());
      } catch {
        applyFallbackRecipients();
      }
    }

    loadScheduler();
  }, [settingsOnly]);

  useEffect(() => {
    if (settingsOnly) return undefined;

    const intervalId = setInterval(async () => {
      try {
        await Promise.all([
          refreshSchedulerStatus(),
          refreshSchedulerHistory(),
        ]);
        setNowTick(Date.now());
      } catch {
        // Keep current status display if polling fails temporarily.
      }
    }, SCHEDULER.POLL_INTERVAL_MS);

    return () => clearInterval(intervalId);
  }, [settingsOnly]);

  function addRecipientFromInput() {
    if (!isAdmin) return;
    const value = recipientInput.trim();
    if (!value) return;
    if (!isValidEmail(value)) {
      setRecipientError("Please enter a valid email address.");
      return;
    }
    if (
      toRecipients.some(
        (recipient) => recipient.toLowerCase() === value.toLowerCase(),
      )
    ) {
      setRecipientError("Recipient already exists.");
      return;
    }

    setRecipients((prev) => [...prev, value]);
    setRecipientInput("");
    setRecipientError(null);
  }

  function removeRecipient(index) {
    if (!isAdmin) return;
    setRecipients((prev) => prev.filter((_, idx) => idx !== index));
  }

  function startRecipientEdit(index) {
    if (!isAdmin) return;
    setEditingRecipientIndex(index);
    setEditingRecipientValue(toRecipients[index] || "");
    setRecipientError(null);
  }

  function saveRecipientEdit() {
    if (!isAdmin) return;
    const value = editingRecipientValue.trim();
    if (!value || !isValidEmail(value)) {
      setRecipientError("Please enter a valid email address.");
      return;
    }

    const hasDuplicate = toRecipients.some(
      (recipient, idx) =>
        idx !== editingRecipientIndex &&
        recipient.toLowerCase() === value.toLowerCase(),
    );

    if (hasDuplicate) {
      setRecipientError("Recipient already exists.");
      return;
    }

    setRecipients((prev) =>
      prev.map((recipient, idx) =>
        idx === editingRecipientIndex ? value : recipient,
      ),
    );
    setEditingRecipientIndex(-1);
    setEditingRecipientValue("");
    setRecipientError(null);
  }

  function addCcFromInput() {
    if (!isAdmin) return;
    const value = ccInput.trim();
    if (!value) return;
    if (!isValidEmail(value)) {
      setCcError("Please enter a valid CC email address.");
      return;
    }
    if (
      ccRecipients.some(
        (recipient) => recipient.toLowerCase() === value.toLowerCase(),
      )
    ) {
      setCcError("CC recipient already exists.");
      return;
    }

    setCc((prev) => [...prev, value]);
    setCcInput("");
    setCcError(null);
  }

  function removeCc(index) {
    if (!isAdmin) return;
    setCc((prev) => prev.filter((_, idx) => idx !== index));
  }

  function buildConfigPayload(autoStartOverride = null) {
    return {
      to: toRecipients.join(","),
      cc: ccRecipients.join(","),
      start_time: startTime,
      subject,
      auto_start:
        autoStartOverride == null ? isSchedulerRunning : autoStartOverride,
    };
  }

  async function persistConfiguration(options = {}) {
    if (!isAdmin) {
      setErrorMsg(
        "Read-only access. Only admins can update scheduler settings.",
      );
      setTimeout(() => setErrorMsg(null), SCHEDULER.TOAST_DURATION_MS);
      return false;
    }

    const { showSuccess = true, autoStartOverride = null } = options;
    if (toRecipients.length === 0) {
      setErrorMsg("Please add at least one recipient.");
      setTimeout(() => setErrorMsg(null), SCHEDULER.TOAST_DURATION_MS);
      return false;
    }

    setSaving(true);
    setSuccessMsg(null);
    setErrorMsg(null);

    try {
      await updateSchedulerConfig(buildConfigPayload(autoStartOverride));
      localStorage.setItem(RECIPIENTS_FALLBACK_KEY, toRecipients.join(","));
      localStorage.setItem(CC_FALLBACK_KEY, ccRecipients.join(","));
      if (autoStartOverride != null) {
        setIsSchedulerRunning(true);
      }
      if (showSuccess) {
        setSuccessMsg("Configuration saved successfully.");
        setTimeout(() => setSuccessMsg(null), SCHEDULER.TOAST_DURATION_MS);
      }
      return true;
    } catch (err) {
      const message = err?.message || "Failed to save configuration";
      const endpointUnavailable =
        /Request failed: 404|Request failed: 405/i.test(message);

      if (endpointUnavailable) {
        // TODO: persist via API when endpoint is available.
        localStorage.setItem(RECIPIENTS_FALLBACK_KEY, toRecipients.join(","));
        localStorage.setItem(CC_FALLBACK_KEY, ccRecipients.join(","));
        setSuccessMsg(
          "Saved locally. Scheduler settings API endpoint is unavailable.",
        );
        setTimeout(() => setSuccessMsg(null), SCHEDULER.TOAST_DURATION_MS);
        return true;
      }

      setErrorMsg(message);
      setTimeout(() => setErrorMsg(null), SCHEDULER.TOAST_DURATION_MS);
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveConfiguration() {
    if (!isAdmin) return;
    const saved = await persistConfiguration({
      showSuccess: true,
      autoStartOverride: true,
    });
    if (saved) {
      await Promise.all([
        refreshSchedulerStatus(),
        settingsOnly ? Promise.resolve() : refreshSchedulerHistory(),
      ]);
      setNowTick(Date.now());
    }
  }

  async function handleSendNow() {
    if (!isAdmin) return;
    if (toRecipients.length === 0) return;
    const previousTopTimestamp = sendHistory?.[0]?.timestamp || null;

    setSending(true);
    setSuccessMsg(null);
    setErrorMsg(null);

    try {
      const synced = await persistConfiguration({
        showSuccess: false,
        autoStartOverride: true,
      });
      if (!synced) {
        return;
      }

      await sendTestEmail({
        to: toRecipients.join(","),
        cc: ccRecipients.join(","),
        subject,
        start_time: startTime,
      });

      await Promise.all([refreshSchedulerStatus(), refreshSchedulerHistory()]);
      await waitForSendHistoryUpdate(previousTopTimestamp);
      setNowTick(Date.now());

      setSuccessMsg(
        "Email trigger accepted. Check Send History for final delivery status.",
      );
      setTimeout(() => setSuccessMsg(null), SCHEDULER.TOAST_DURATION_MS);
    } catch (err) {
      await Promise.all([
        refreshSchedulerStatus(),
        refreshSchedulerHistory(),
      ]).catch(() => {});
      setErrorMsg(err.message || "Failed to send email");
      setTimeout(() => setErrorMsg(null), SCHEDULER.TOAST_DURATION_MS);
    } finally {
      setSending(false);
    }
  }

  async function handleStopScheduler() {
    if (!isAdmin) return;
    setScheduling(true);
    setSuccessMsg(null);
    setErrorMsg(null);

    try {
      const stopResult = await stopSchedulerApi();
      await Promise.all([
        refreshSchedulerStatus(),
        settingsOnly ? Promise.resolve() : refreshSchedulerHistory(),
      ]);
      setNowTick(Date.now());

      const running =
        String(stopResult?.status || "").toLowerCase() === "running";
      setIsSchedulerRunning(running);
      setSuccessMsg(
        running ? "Scheduler is still running." : "Scheduler stopped.",
      );
      setTimeout(() => setSuccessMsg(null), SCHEDULER.TOAST_DURATION_MS);
    } catch (err) {
      setErrorMsg(err.message || "Failed to stop scheduler");
      setTimeout(() => setErrorMsg(null), SCHEDULER.TOAST_DURATION_MS);
    } finally {
      setScheduling(false);
    }
  }

  const leftPanel = (
    <div className="rounded-xl border border-slate-200 bg-white p-5 space-y-5 animate-slide-up">
      <div className="flex items-center justify-between border-b border-slate-100 pb-2">
        <h2 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
          <Mail className="w-4 h-4 text-blue-600" />
          Email Configuration
        </h2>
      </div>

      <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-4 space-y-3">
        <label className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-600 uppercase tracking-wide">
          <Users className="w-3.5 h-3.5 text-slate-400" />
          Recipients (To)
        </label>

        {/* ADDED: Recipient tags/chips with edit/remove controls. */}
        <div className="min-h-10 rounded-md border border-slate-200 bg-white px-2 py-2 flex flex-wrap items-center gap-2">
          {toRecipients.length === 0 ? (
            <span className="text-xs text-slate-400">No recipients added.</span>
          ) : (
            toRecipients.map((email, idx) => (
              <span
                key={`${email}-${idx}`}
                className="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs text-blue-700"
              >
                {email}
                <button
                  onClick={() => startRecipientEdit(idx)}
                  disabled={!isAdmin}
                  className="text-blue-500 hover:text-blue-700"
                  title="Edit recipient"
                >
                  <Pencil className="w-3 h-3" />
                </button>
                <button
                  onClick={() => removeRecipient(idx)}
                  disabled={!isAdmin}
                  className="text-blue-500 hover:text-red-600"
                  title="Remove recipient"
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            ))
          )}
        </div>

        {editingRecipientIndex >= 0 && (
          <div className="flex items-center gap-2">
            <input
              type="email"
              value={editingRecipientValue}
              onChange={(e) => setEditingRecipientValue(e.target.value)}
              disabled={!isAdmin}
              className="w-full px-3 py-2 text-sm border border-slate-200 rounded-md outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
            />
            <button
              onClick={saveRecipientEdit}
              disabled={!isAdmin}
              className="px-3 py-2 text-xs font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700"
            >
              Save
            </button>
            <button
              onClick={() => {
                setEditingRecipientIndex(-1);
                setEditingRecipientValue("");
              }}
              disabled={!isAdmin}
              className="px-3 py-2 text-xs font-medium rounded-md border border-slate-200 hover:bg-slate-100"
            >
              Cancel
            </button>
          </div>
        )}

        <div className="flex items-center gap-2">
          <input
            type="email"
            value={recipientInput}
            onChange={(e) => {
              setRecipientInput(e.target.value);
              if (recipientError) setRecipientError(null);
            }}
            disabled={!isAdmin}
            placeholder="Add recipient email"
            className="w-full px-3 py-2 text-sm border border-slate-200 rounded-md outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addRecipientFromInput();
              }
            }}
          />
          <button
            onClick={addRecipientFromInput}
            disabled={!isAdmin}
            className="inline-flex items-center gap-1 px-3 py-2 text-xs font-medium rounded-md border border-slate-200 hover:bg-slate-100"
          >
            <Plus className="w-3.5 h-3.5" />
            Add Recipient
          </button>
        </div>
        {recipientError && (
          <p className="text-xs text-red-600">{recipientError}</p>
        )}
      </div>

      <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-4 space-y-3">
        <label className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-600 uppercase tracking-wide">
          <Mail className="w-3.5 h-3.5 text-slate-400" />
          CC
        </label>

        <div className="min-h-10 rounded-md border border-slate-200 bg-white px-2 py-2 flex flex-wrap items-center gap-2">
          {ccRecipients.length === 0 ? (
            <span className="text-xs text-slate-400">No CC recipients.</span>
          ) : (
            ccRecipients.map((email, idx) => (
              <span
                key={`${email}-${idx}`}
                className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs text-slate-700"
              >
                {email}
                <button
                  onClick={() => removeCc(idx)}
                  disabled={!isAdmin}
                  className="text-slate-400 hover:text-red-600"
                  title="Remove CC"
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            ))
          )}
        </div>

        <div className="flex items-center gap-2">
          <input
            type="email"
            value={ccInput}
            onChange={(e) => {
              setCcInput(e.target.value);
              if (ccError) setCcError(null);
            }}
            disabled={!isAdmin}
            placeholder="Add CC email"
            className="w-full px-3 py-2 text-sm border border-slate-200 rounded-md outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addCcFromInput();
              }
            }}
          />
          <button
            onClick={addCcFromInput}
            disabled={!isAdmin}
            className="inline-flex items-center gap-1 px-3 py-2 text-xs font-medium rounded-md border border-slate-200 hover:bg-slate-100"
          >
            <Plus className="w-3.5 h-3.5" />
            Add CC
          </button>
        </div>
        {ccError && <p className="text-xs text-red-600">{ccError}</p>}
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
            disabled
            className="w-full px-3 py-2 text-sm border border-slate-200 rounded-md bg-[#f0f0f0] text-[#888888] cursor-not-allowed"
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
            disabled={!isAdmin}
            className="w-full px-3 py-2 text-sm border border-slate-200 rounded-md outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
          />
        </div>
      </div>

      <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-4 mt-4">
        <div className="flex items-center justify-between gap-2 mb-3">
          <h2 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
            <CalendarClock className="w-4 h-4 text-blue-600" />
            Execution Controls
          </h2>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 [&>button]:px-3 [&>button]:py-2">
          <button
            onClick={handleSaveConfiguration}
            disabled={saving || scheduling || sending || !isAdmin}
            className="inline-flex items-center justify-center gap-2 w-full bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Save className="w-4 h-4" />
            {saving ? "Saving..." : "Save & Schedule"}
          </button>
          <button
            onClick={handleStopScheduler}
            disabled={scheduling || saving || sending || !isAdmin}
            title={`Stop Scheduler — Today: ${today}`}
            className="inline-flex items-center justify-center gap-2 w-full border border-red-200 text-red-600 text-sm font-medium rounded-md hover:bg-red-50 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <StopCircle className="w-4 h-4" />
            Stop Clock
          </button>
          <button
            onClick={handleSendNow}
            disabled={sending || saving || scheduling || !isAdmin}
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
          Save & Schedule arms the system automatically. Send Test Now sends one
          immediate email without changing scheduler state.
        </p>
      </div>
    </div>
  );

  const rightPanel = (
    <div
      className="space-y-4 animate-slide-up"
      style={{ animationDelay: "100ms" }}
    >
      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <h3 className="text-xs font-semibold tracking-wide uppercase text-slate-500 mb-2">
          Activity Snapshot
        </h3>
        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-md border border-slate-200 bg-slate-50 px-2.5 py-2">
            <p className="text-[11px] text-slate-500">Total Sends</p>
            <p className="text-sm font-semibold text-slate-800">
              {totalSendsCount}
            </p>
          </div>
          <div className="rounded-md border border-slate-200 bg-slate-50 px-2.5 py-2">
            <p className="text-[11px] text-slate-500">Configured Time</p>
            <p className="text-sm font-semibold text-slate-800">{startTime}</p>
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white overflow-hidden flex flex-col">
        <div className="px-5 py-4 border-b border-slate-100 flex items-center gap-2">
          <History className="w-4 h-4 text-blue-600" />
          <h2 className="text-sm font-semibold text-slate-800">Send History</h2>
        </div>

        {displayedSendHistory.length === 0 ? (
          <div className="p-4">
            <p className="text-xs text-slate-400 text-center">
              No send history available.
            </p>
          </div>
        ) : (
          <div className="overflow-y-auto max-h-[80vh] flex-1">
            {/* ADDED: Compact 3-column send history layout with explicit widths for proper alignment. */}
            <table className="energy-table w-full text-xs text-left table-fixed">
              <colgroup>
                <col className="w-[38%]" />
                <col className="w-[22%]" />
                <col className="w-[40%]" />
              </colgroup>
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <th className="px-3 py-2 font-semibold text-slate-500 text-left whitespace-nowrap align-middle">
                    Date &amp; Time
                  </th>
                  <th className="px-3 py-2 font-semibold text-slate-500 text-left whitespace-nowrap align-middle">
                    Status
                  </th>
                  <th className="px-3 py-2 font-semibold text-slate-500 text-left whitespace-nowrap align-middle">
                    Recipients (To)
                  </th>
                </tr>
              </thead>
              <tbody>
                {displayedSendHistory.map((entry, idx) => (
                  <tr
                    key={`${entry.timestamp || "no-ts"}-${idx}`}
                    className="border-b border-slate-100 align-top"
                  >
                    <td className="px-3 py-2 text-slate-700 whitespace-nowrap">
                      {formatDateTime(entry.timestamp)}
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={`text-[10px] uppercase font-bold tracking-wide px-2 py-1 rounded-full inline-flex ${
                          String(entry.status || "").toLowerCase() === "success"
                            ? "bg-emerald-50 text-emerald-600 border border-emerald-100"
                            : String(entry.status || "").toLowerCase() ===
                                "failed"
                              ? "bg-red-50 text-red-500 border border-red-100"
                              : "bg-slate-50 text-slate-600 border border-slate-200"
                        }`}
                      >
                        {entry.status || "—"}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-slate-700 align-top whitespace-normal break-all">
                      {entry.recipients || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );

  const HeaderIcon = asSettings ? Settings : CalendarClock;
  const headerTitle = asSettings ? "Settings" : "Scheduler Dashboard";

  return (
    <div className="px-8 py-6 bg-gray-100 rounded-3xl space-y-5">
      {settingsOnly ? (
        <div className="rounded-xl border border-slate-200 bg-white px-6 py-5 animate-fade-in">
          <h1 className="text-lg font-semibold text-slate-900 flex items-center gap-2">
            <Settings className="w-5 h-5 text-blue-600" />
            Settings
          </h1>
          <p className="text-xs text-slate-500 mt-1">Scheduler Settings</p>
          <div className="mt-2">
            {isAdmin ? (
              <span className="text-sm font-medium text-green-600 bg-green-50 px-2 py-1 rounded">
                Admin Access (Edit Mode)
              </span>
            ) : (
              <span className="text-sm font-medium text-slate-500 bg-slate-100 px-2 py-1 rounded">
                Read-Only Access
              </span>
            )}
          </div>
        </div>
      ) : (
        <div className="rounded-xl border border-slate-200 bg-white px-6 py-5 animate-fade-in">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="text-lg font-semibold text-slate-900 flex items-center gap-2">
                <HeaderIcon className="w-5 h-5 text-blue-600" />
                {headerTitle}
              </h1>
              {asSettings && (
                <p className="text-xs text-slate-500 mt-1">
                  Scheduler Settings
                </p>
              )}
              <div className="mt-2">
                {isAdmin ? (
                  <span className="text-sm font-medium text-green-600 bg-green-50 px-2 py-1 rounded">
                    Admin Access (Edit Mode)
                  </span>
                ) : (
                  <span className="text-sm font-medium text-slate-500 bg-slate-100 px-2 py-1 rounded">
                    Read-Only Access
                  </span>
                )}
              </div>
            </div>
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

          <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-4 border-t border-slate-100 pt-4">
            <div>
              <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1 flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5" /> Next Scheduled Mail
              </p>
              <p className="text-sm font-medium text-slate-800">
                {nextScheduledMailLabel !== "—"
                  ? nextScheduledMailLabel
                  : isSchedulerRunning
                    ? nextRunLabel
                    : "—"}
              </p>
            </div>
            <div className="min-w-0">
              <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1 flex items-center gap-1.5">
                <Users className="w-3.5 h-3.5" /> Recipients (To)
              </p>
              <p
                className="text-sm font-medium text-slate-800 whitespace-normal break-all"
                title={toRecipients.join(", ")}
              >
                {toRecipients.length
                  ? toRecipients.join(", ")
                  : "None configured"}
              </p>
            </div>
            <div className="min-w-0">
              <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1 flex items-center gap-1.5">
                <Mail className="w-3.5 h-3.5" /> CC
              </p>
              <p
                className="text-sm font-medium text-slate-800 whitespace-normal break-all"
                title={ccRecipients.join(", ")}
              >
                {ccRecipients.length
                  ? ccRecipients.join(", ")
                  : "None configured"}
              </p>
            </div>
          </div>
        </div>
      )}

      {successMsg && (
        <div className="flex items-center gap-2 text-sm text-emerald-700 border border-emerald-200 bg-emerald-50 px-3 py-2 rounded-lg animate-fade-in">
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
        <div className="flex items-center gap-2 text-sm text-red-600 border border-red-200 bg-red-50 px-3 py-2 rounded-lg animate-fade-in">
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

      <div
        className={`grid gap-5 ${settingsOnly ? "grid-cols-1" : "xl:grid-cols-12"}`}
      >
        <div className={settingsOnly ? "" : "xl:col-span-8"}>{leftPanel}</div>
        {!settingsOnly && <div className="xl:col-span-4">{rightPanel}</div>}
      </div>
    </div>
  );
}
