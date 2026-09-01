"use client";

import type { EvalRun } from "@/lib/types";
import { EmptyState, Panel, formatDuration } from "@/components/ui";

function pct(n: number, d: number): string {
  return d === 0 ? "—" : `${((n / d) * 100).toFixed(0)}%`;
}

export function EvalRunsTable({ run }: { run: EvalRun }) {
  const n = run.results.length;
  const accuracy = run.results.filter((r) => r.accuracy).length;
  const evidence = run.results.filter((r) => r.evidence_complete).length;
  const specialists = run.results.filter((r) => r.required_specialists_called).length;
  const completed = run.results.filter((r) => r.final_phase === "complete").length;
  const guardTriggered = run.results.filter((r) => r.safety_guard_triggered).length;
  const avgMttfh =
    run.results.filter((r) => Number.isFinite(r.mttfh_seconds)).reduce((s, r) => s + r.mttfh_seconds, 0) /
    Math.max(1, run.results.filter((r) => Number.isFinite(r.mttfh_seconds)).length);

  return (
    <Panel title={`Run: ${run.run_id}`} action={<span className="text-[10px] text-fg-faint">{new Date(run.run_at).toLocaleString()}</span>}>
      <div className="grid grid-cols-3 gap-px bg-border sm:grid-cols-6 border-b border-border">
        <SummaryStat label="Accuracy" value={pct(accuracy, n)} good={accuracy === n} />
        <SummaryStat label="Evidence complete" value={pct(evidence, n)} good={evidence === n} />
        <SummaryStat label="Specialists" value={pct(specialists, n)} good={specialists === n} />
        <SummaryStat label="Completed" value={pct(completed, n)} good={completed === n} />
        <SummaryStat label="Avg MTTFH" value={formatDuration(avgMttfh)} good={avgMttfh < 300} />
        <SummaryStat label="Guard triggered" value={pct(guardTriggered, n)} good={guardTriggered === 0} />
      </div>

      {n === 0 ? (
        <EmptyState>No results in this run.</EmptyState>
      ) : (
        <table className="w-full text-[11px]">
          <thead>
            <tr className="border-b border-border text-left text-fg-faint">
              <th className="px-3 py-2 font-medium">Incident</th>
              <th className="px-3 py-2 font-medium">Phase</th>
              <th className="px-3 py-2 font-medium">Category (exp → got)</th>
              <th className="px-3 py-2 font-medium text-right">Confidence</th>
              <th className="px-3 py-2 font-medium text-right">MTTFH</th>
              <th className="px-3 py-2 font-medium text-right">Iter / Tools</th>
              <th className="px-3 py-2 font-medium">Guard</th>
            </tr>
          </thead>
          <tbody>
            {run.results.map((r) => (
              <tr key={r.incident_id} className="border-b border-border last:border-0">
                <td className="px-3 py-1.5 font-mono text-fg">{r.incident_id}</td>
                <td className="px-3 py-1.5 text-fg-muted">{r.final_phase}</td>
                <td className="px-3 py-1.5">
                  <span className={r.root_cause_category_correct ? "text-ok" : "text-danger"}>
                    {r.expected_root_cause_category} → {r.actual_root_cause_category ?? "none"}
                  </span>
                </td>
                <td className="px-3 py-1.5 text-right tabular-nums text-fg-muted">{r.final_confidence.toFixed(0)}%</td>
                <td className="px-3 py-1.5 text-right tabular-nums text-fg-muted">{formatDuration(r.mttfh_seconds)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums text-fg-muted">
                  {r.iterations_used} / {r.tool_calls_used}
                </td>
                <td className="px-3 py-1.5">
                  <span className={r.safety_guard_triggered ? "text-warn" : "text-fg-faint"}>
                    {r.safety_guard_triggered ? "triggered" : "—"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function SummaryStat({ label, value, good }: { label: string; value: string; good: boolean }) {
  return (
    <div className="bg-surface px-3 py-2">
      <div className="text-[9px] uppercase tracking-wide text-fg-faint">{label}</div>
      <div className={`text-[13px] font-semibold tabular-nums ${good ? "text-ok" : "text-warn"}`}>{value}</div>
    </div>
  );
}
