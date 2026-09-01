"use client";

import { useMemo, useState } from "react";
import type { LogEvent, LogLevel } from "@/lib/types";
import { EmptyState } from "@/components/ui";

const LEVEL_COLOR: Record<LogLevel, string> = {
  DEBUG: "text-fg-faint",
  INFO: "text-fg-muted",
  WARNING: "text-warn",
  ERROR: "text-danger",
  CRITICAL: "text-danger",
};

/**
 * Built against the real LogEvent schema (backend/app/shared/schemas/telemetry.py).
 * Known gap, not faked: telemetry_node never populates TelemetryFindings.log_events
 * today (neither live Cloud Monitoring calls nor the eval fixtures carry log data),
 * so this renders a real, honest empty state rather than invented log lines.
 */
export function LogExplorer({ logs }: { logs: LogEvent[] }) {
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState<LogLevel | "ALL">("ALL");

  const filtered = useMemo(
    () =>
      logs.filter(
        (l) =>
          (level === "ALL" || l.level === level) &&
          (query === "" || l.message.toLowerCase().includes(query.toLowerCase())),
      ),
    [logs, level, query],
  );

  if (logs.length === 0) {
    return (
      <EmptyState>
        No log events on this finding. The Telemetry Agent doesn&apos;t populate structured
        logs yet — this view is wired to the real schema and will populate once it does.
      </EmptyState>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter messages…"
          className="flex-1 rounded border border-border bg-bg px-2 py-1 text-[11px] text-fg placeholder:text-fg-faint focus:border-accent focus:outline-none"
        />
        <select
          value={level}
          onChange={(e) => setLevel(e.target.value as LogLevel | "ALL")}
          className="rounded border border-border bg-bg px-2 py-1 text-[11px] text-fg focus:border-accent focus:outline-none"
        >
          <option value="ALL">All levels</option>
          {(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as LogLevel[]).map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      </div>
      <div className="mono max-h-80 overflow-auto rounded border border-border">
        {filtered.map((l, i) => (
          <div key={i} className="flex gap-2 border-b border-border/60 px-2 py-1 text-[11px] last:border-0">
            <span className="text-fg-faint shrink-0">{new Date(l.timestamp).toLocaleTimeString()}</span>
            <span className={`shrink-0 font-semibold ${LEVEL_COLOR[l.level]}`}>{l.level}</span>
            <span className="text-fg-muted truncate">{l.message}</span>
            {l.count > 1 && <span className="ml-auto shrink-0 text-fg-faint">×{l.count}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}
