"use client";

import { useEffect, useState } from "react";
import type { InvestigationAnalysisResponse, InvestigationStatusResponse } from "@/lib/types";
import { PhasePill, SeverityBadge, formatDuration } from "@/components/ui";

export function StatusHeader({
  status,
  analysis,
}: {
  status: InvestigationStatusResponse;
  analysis: InvestigationAnalysisResponse | null;
}) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const started = new Date(status.started_at).getTime();
  const ended = status.completed_at ? new Date(status.completed_at).getTime() : now;
  const elapsedSec = Math.max(0, (ended - started) / 1000);

  const cost = analysis?.token_log.reduce((sum, t) => sum + t.cost_usd, 0) ?? 0;
  const totalTokens = analysis
    ? analysis.token_log.reduce((sum, t) => sum + t.input_tokens + t.output_tokens, 0)
    : 0;

  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-border bg-surface px-4 py-3">
      <div className="flex items-center gap-2">
        <SeverityBadge severity={status.incident.severity} />
        <h1 className="font-semibold text-fg text-[14px]">{status.incident.alert_name}</h1>
      </div>

      <PhasePill phase={status.phase} />

      <div className="flex items-center gap-4 text-[11px] text-fg-muted ml-auto">
        <Stat label="Service" value={status.incident.service_name} />
        <Stat label="Elapsed" value={formatDuration(elapsedSec)} />
        <Stat label="Iterations" value={`${status.iterations_used}`} />
        <Stat label="Tool calls" value={`${status.tool_calls_used}`} />
        <Stat label="Tokens" value={totalTokens.toLocaleString()} />
        <Stat label="Cost" value={`$${cost.toFixed(4)}`} />
        {status.working_hypothesis && (
          <Stat label="Confidence" value={`${status.working_confidence.toFixed(0)}%`} />
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col items-end leading-tight">
      <span className="text-fg-faint text-[9px] uppercase tracking-wide">{label}</span>
      <span className="tabular-nums text-fg">{value}</span>
    </div>
  );
}
