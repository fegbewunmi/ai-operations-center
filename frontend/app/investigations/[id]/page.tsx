"use client";

import { use, useCallback, useState } from "react";
import { useInvestigationPolling } from "@/lib/useInvestigationPolling";
import type { GraphNode } from "@/lib/deriveEvidenceLinks";
import { StatusHeader } from "@/components/workspace/StatusHeader";
import { HumanControls } from "@/components/workspace/HumanControls";
import { InvestigationGraph } from "@/components/workspace/InvestigationGraph";
import { EvidenceNav } from "@/components/workspace/EvidenceNav";
import { HypothesisPanel } from "@/components/workspace/HypothesisPanel";
import { AgentActivityPanel } from "@/components/workspace/AgentActivityPanel";
import { NodeDetailPanel } from "@/components/workspace/NodeDetailPanel";

export default function InvestigationWorkspacePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { status, evidence, analysis, timeline, loading, error } = useInvestigationPolling(id);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedKind, setSelectedKind] = useState<GraphNode["kind"] | null>(null);

  const selectFromGraph = useCallback((nodeId: string, kind: GraphNode["kind"]) => {
    setSelectedId(nodeId);
    setSelectedKind(kind);
  }, []);

  const selectEvidence = useCallback((nodeId: string) => {
    setSelectedId(nodeId);
    setSelectedKind("evidence");
  }, []);

  const selectHypothesis = useCallback((nodeId: string) => {
    setSelectedId(nodeId);
    setSelectedKind("hypothesis");
  }, []);

  if (loading && !status) {
    return <div className="p-6 text-[12px] text-fg-faint">Loading investigation…</div>;
  }

  if (error && !status) {
    return <div className="p-6 text-[12px] text-danger">{error}</div>;
  }

  if (!status) return null;

  const hypotheses = analysis?.analysis_output?.hypotheses ?? [];
  const feedback = analysis?.human_feedback ?? [];

  return (
    <div className="flex flex-1 min-h-0 flex-col">
      <StatusHeader status={status} analysis={analysis} />

      {analysis && analysis.pending_approvals.some((p) => p.approved === null) && (
        <div className="border-b border-border px-4 py-3">
          <HumanControls investigationId={id} analysis={analysis} onSubmitted={() => {}} />
        </div>
      )}

      <div className="flex flex-1 min-h-0 gap-3 p-3">
        <div className="w-56 shrink-0 flex flex-col min-h-0">
          <EvidenceNav evidence={evidence} selectedId={selectedKind === "evidence" ? selectedId : null} onSelect={selectEvidence} />
        </div>

        <div className="flex flex-1 min-h-0 min-w-0 flex-col gap-3">
          <div className="min-h-0 flex-1 flex flex-col rounded-md border border-border bg-surface">
            <InvestigationGraph
              incident={status.incident}
              evidence={evidence}
              hypotheses={hypotheses}
              selectedId={selectedId}
              onSelect={selectFromGraph}
            />
          </div>
          <div className="min-h-0 flex-1 flex flex-col">
            <NodeDetailPanel
              selectedId={selectedId}
              selectedKind={selectedKind}
              evidence={evidence}
              hypotheses={hypotheses}
            />
          </div>
        </div>

        <div className="w-80 shrink-0 flex flex-col gap-3 min-h-0">
          <div className="flex-1 min-h-0 flex flex-col">
            <HypothesisPanel
              investigationId={id}
              hypotheses={hypotheses}
              feedback={feedback}
              onFeedbackSubmitted={() => {}}
              selectedId={selectedKind === "hypothesis" ? selectedId : null}
              onSelect={selectHypothesis}
            />
          </div>
          <div className="flex-1 min-h-0 flex flex-col">
            <AgentActivityPanel status={status} analysis={analysis} timeline={timeline} />
          </div>
        </div>
      </div>
    </div>
  );
}
