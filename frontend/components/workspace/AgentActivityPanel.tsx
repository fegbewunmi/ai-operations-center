"use client";

import type {
  InvestigationAnalysisResponse,
  InvestigationPhase,
  InvestigationStatusResponse,
  InvestigationTimelineResponse,
  TimelineEvent,
} from "@/lib/types";
import { EmptyState, Panel, formatRelativeTime } from "@/components/ui";

// Maps the two node-naming schemes the backend uses (TimelineEvent.source vs
// NodeTokenUsage.node) to one display label. See app/graph/nodes/*.py for the
// literal strings each node writes.
const NODE_LABELS: Record<string, string> = {
  planner: "Planner",
  telemetry_agent: "Telemetry",
  telemetry: "Telemetry",
  deployment_agent: "Deployment",
  deployment: "Deployment",
  knowledge_agent: "Knowledge",
  knowledge: "Knowledge",
  incident_analysis: "Incident Analysis",
  synthesizer: "Synthesizer",
  safety_guard: "Safety Guard",
  action_dispatcher: "Dispatcher",
};

function label(source: string): string {
  return NODE_LABELS[source] ?? source;
}

const PHASE_ACTIVITY: Record<InvestigationPhase, string> = {
  planning: "Planner is deciding the next step…",
  investigating: "A specialist agent is gathering evidence…",
  synthesizing: "Incident Analysis / Synthesizer is reasoning over evidence…",
  responding: "Dispatching action or awaiting human approval…",
  complete: "Investigation complete.",
  escalated: "Investigation escalated.",
};

export function AgentActivityPanel({
  status,
  analysis,
  timeline,
}: {
  status: InvestigationStatusResponse;
  analysis: InvestigationAnalysisResponse | null;
  timeline: InvestigationTimelineResponse | null;
}) {
  const events = timeline?.events ?? [];
  const lastEvent: TimelineEvent | undefined = events[events.length - 1];

  const byNode = new Map<string, { inTok: number; outTok: number; cost: number; calls: number }>();
  for (const t of analysis?.token_log ?? []) {
    const key = label(t.node);
    const cur = byNode.get(key) ?? { inTok: 0, outTok: 0, cost: 0, calls: 0 };
    cur.inTok += t.input_tokens;
    cur.outTok += t.output_tokens;
    cur.cost += t.cost_usd;
    cur.calls += 1;
    byNode.set(key, cur);
  }

  return (
    <Panel title="Agent activity" className="flex-1 min-h-0 flex flex-col">
      <div className="flex-1 min-h-0 flex flex-col">
        <div className="border-b border-border px-3 py-2.5 text-[13px] text-fg">
          {PHASE_ACTIVITY[status.phase]}
          {lastEvent && (
            <div className="mt-1 text-[12px] text-fg-muted">
              Last: <span className="text-fg-muted">{label(lastEvent.source)}</span> -{" "}
              {formatRelativeTime(lastEvent.timestamp)}
            </div>
          )}
        </div>

        {byNode.size > 0 && (
          <table className="w-full text-[12px] border-b border-border shrink-0">
            <thead>
              <tr className="text-left text-fg-faint">
                <th className="px-3 py-2 font-medium">Node</th>
                <th className="px-3 py-2 font-medium text-right">Calls</th>
                <th className="px-3 py-2 font-medium text-right">Tokens</th>
                <th className="px-3 py-2 font-medium text-right">Cost</th>
              </tr>
            </thead>
            <tbody>
              {[...byNode.entries()].map(([node, stats]) => (
                <tr key={node} className="border-t border-border/60">
                  <td className="px-3 py-1.5 text-fg">{node}</td>
                  <td className="px-3 py-1.5 text-right text-fg-muted tabular-nums">{stats.calls}</td>
                  <td className="px-3 py-1.5 text-right text-fg-muted tabular-nums">
                    {(stats.inTok + stats.outTok).toLocaleString()}
                  </td>
                  <td className="px-3 py-1.5 text-right text-fg-muted tabular-nums">${stats.cost.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="flex-1 min-h-[140px] overflow-auto">
          {events.length === 0 ? (
            <EmptyState>No activity yet.</EmptyState>
          ) : (
            <ul className="divide-y divide-border/60">
              {[...events].reverse().map((e, i) => (
                <li key={i} className="px-3 py-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[12px] font-medium text-fg">{label(e.source)}</span>
                    <span className="text-[11px] text-fg-faint tabular-nums">{formatRelativeTime(e.timestamp)}</span>
                  </div>
                  <p className="mt-1 text-[12px] text-fg-muted leading-snug">{e.description}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Panel>
  );
}
