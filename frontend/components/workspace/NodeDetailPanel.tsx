"use client";

import type { GraphNode } from "@/lib/deriveEvidenceLinks";
import type { Hypothesis, InvestigationEvidenceResponse } from "@/lib/types";
import { MetricsChart } from "@/components/evidence/MetricsChart";
import { LogExplorer } from "@/components/evidence/LogExplorer";
import { DeploymentTimeline } from "@/components/evidence/DeploymentTimeline";
import { KnowledgeViewer } from "@/components/evidence/KnowledgeViewer";
import { AuthorityBadge, CategoryDot, ConfidenceBar, EmptyState, Panel } from "@/components/ui";

function findNode(evidence: InvestigationEvidenceResponse | null, id: string) {
  if (!evidence) return null;
  const [kind, idxStr] = id.split("-");
  const idx = Number(idxStr);
  if (kind === "telemetry") return { kind, data: evidence.telemetry_findings[idx] } as const;
  if (kind === "deployment") return { kind, data: evidence.deployment_findings[idx] } as const;
  if (kind === "knowledge") return { kind, data: evidence.knowledge_context[idx] } as const;
  return null;
}

export function NodeDetailPanel({
  selectedId,
  selectedKind,
  evidence,
  hypotheses,
}: {
  selectedId: string | null;
  selectedKind: GraphNode["kind"] | null;
  evidence: InvestigationEvidenceResponse | null;
  hypotheses: Hypothesis[];
}) {
  if (!selectedId || selectedKind === "incident") {
    return (
      <Panel title="Detail" className="flex-1 min-h-0">
        <EmptyState>Select a node in the graph, or an item in the evidence list, to inspect it.</EmptyState>
      </Panel>
    );
  }

  if (selectedKind === "hypothesis") {
    const h = hypotheses.find((h) => h.hypothesis_id === selectedId);
    if (!h) {
      return (
        <Panel title="Detail" className="flex-1 min-h-0">
          <EmptyState>Hypothesis no longer present.</EmptyState>
        </Panel>
      );
    }
    return (
      <Panel title="Hypothesis detail" className="flex-1 min-h-0">
        <div className="overflow-auto p-3 flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <CategoryDot category={h.root_cause_category} />
            <span className="text-[11px] uppercase tracking-wide text-fg-muted">{h.root_cause_category}</span>
            <AuthorityBadge level={h.authority_level} />
          </div>
          <p className="text-[13px] text-fg leading-relaxed">{h.description}</p>
          <ConfidenceBar pct={h.confidence_pct} />
          <p className="text-[12px] text-fg-muted">
            <span className="text-fg-faint">Affected service: </span>
            {h.affected_service}
          </p>
          <p className="text-[12px] text-fg-muted">
            <span className="text-fg-faint">Recommended action: </span>
            {h.recommended_action}
          </p>
        </div>
      </Panel>
    );
  }

  const found = findNode(evidence, selectedId);
  if (!found || !found.data) {
    return (
      <Panel title="Detail" className="flex-1 min-h-0">
        <EmptyState>No data for this node.</EmptyState>
      </Panel>
    );
  }

  if (found.kind === "telemetry") {
    const f = found.data;
    return (
      <Panel title={`Telemetry · ${f.service}`} className="flex-1 min-h-0">
        <div className="overflow-auto p-3 flex flex-col gap-3">
          <p className="text-[12px] text-fg-muted leading-relaxed">{f.summary}</p>
          {(f.error_rate_change_pct !== null || f.latency_p99_change_pct !== null) && (
            <div className="flex gap-4 text-[11px]">
              {f.error_rate_change_pct !== null && (
                <span>
                  <span className="text-fg-faint">Error rate change </span>
                  <span className="font-medium text-fg">{f.error_rate_change_pct.toFixed(1)}%</span>
                </span>
              )}
              {f.latency_p99_change_pct !== null && (
                <span>
                  <span className="text-fg-faint">p99 latency change </span>
                  <span className="font-medium text-fg">{f.latency_p99_change_pct.toFixed(1)}%</span>
                </span>
              )}
            </div>
          )}
          {f.error && <p className="text-[11px] text-danger">{f.error}</p>}
          <MetricsChart metrics={f.key_metrics} />
          <LogExplorer logs={f.log_events} />
        </div>
      </Panel>
    );
  }

  if (found.kind === "deployment") {
    const f = found.data;
    return (
      <Panel title={`Deployments · ${f.service}`} className="flex-1 min-h-0">
        <div className="overflow-auto p-3 flex flex-col gap-3">
          <p className="text-[12px] text-fg-muted leading-relaxed">{f.summary}</p>
          {f.error && <p className="text-[11px] text-danger">{f.error}</p>}
          <DeploymentTimeline deployments={f.deployments} />
        </div>
      </Panel>
    );
  }

  const k = found.data;
  return (
    <Panel title="Knowledge" className="flex-1 min-h-0">
      <div className="overflow-auto p-3 flex flex-col gap-3">
        <p className="text-[12px] text-fg-muted leading-relaxed">{k.summary}</p>
        {k.error && <p className="text-[11px] text-danger">{k.error}</p>}
        <KnowledgeViewer context={k} />
      </div>
    </Panel>
  );
}
