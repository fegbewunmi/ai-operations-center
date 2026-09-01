"use client";

import { useMemo } from "react";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Handle,
  Position,
  type Edge,
  type Node,
  type NodeProps,
} from "reactflow";
import dagre from "dagre";
import {
  deriveInvestigationGraph,
  type EvidenceGraphNode,
  type GraphEdge,
  type GraphNode,
  type HypothesisGraphNode,
  type IncidentGraphNode,
} from "@/lib/deriveEvidenceLinks";
import type { Hypothesis, IncidentTrigger, InvestigationEvidenceResponse } from "@/lib/types";
import { CATEGORY_COLOR } from "@/components/ui";

const SPECIALIST_LABEL: Record<EvidenceGraphNode["specialist"], string> = {
  telemetry: "Telemetry",
  deployment: "Deployment",
  knowledge: "Knowledge",
};

function IncidentNodeCard({ data }: NodeProps<IncidentGraphNode>) {
  return (
    <div className="w-56 rounded-md border-2 border-danger/50 bg-surface-raised px-3 py-2 shadow-sm">
      <Handle type="source" position={Position.Right} className="!bg-danger" />
      <div className="text-[9px] uppercase tracking-wide text-danger font-semibold">Incident · {data.severity}</div>
      <div className="mt-0.5 text-[12px] font-medium text-fg leading-snug">{data.alertName}</div>
      <div className="mt-1 text-[10px] text-fg-muted">{data.service}</div>
    </div>
  );
}

function EvidenceNodeCard({ data, selected }: NodeProps<EvidenceGraphNode>) {
  return (
    <div
      className={`w-60 rounded-md border bg-surface-raised px-3 py-2 shadow-sm transition-colors ${
        selected ? "border-accent" : data.hasAnomaly ? "border-warn/50" : "border-border"
      }`}
    >
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <div className="flex items-center justify-between">
        <span className="text-[9px] uppercase tracking-wide text-fg-faint font-semibold">
          {SPECIALIST_LABEL[data.specialist]}
        </span>
        <span className="text-[9px] text-fg-muted">{data.badge}</span>
      </div>
      <div className="mt-0.5 text-[10px] text-fg-muted">{data.service}</div>
      <p className="mt-1 text-[11px] text-fg leading-snug line-clamp-2">{data.summary}</p>
    </div>
  );
}

function HypothesisNodeCard({ data, selected }: NodeProps<HypothesisGraphNode>) {
  const h: Hypothesis = data.hypothesis;
  const color = CATEGORY_COLOR[h.root_cause_category];
  return (
    <div
      className="w-64 rounded-md border-2 bg-surface-raised px-3 py-2 shadow-sm transition-colors"
      style={{ borderColor: selected ? "var(--accent)" : color }}
    >
      <Handle type="target" position={Position.Left} />
      <div className="flex items-center justify-between">
        <span className="text-[9px] uppercase tracking-wide font-semibold" style={{ color }}>
          {h.root_cause_category}
        </span>
        <span className="text-[11px] font-semibold tabular-nums text-fg">{h.confidence_pct.toFixed(0)}%</span>
      </div>
      <p className="mt-1 text-[11px] text-fg leading-snug line-clamp-3">{h.description}</p>
      <div className="mt-1 h-1 rounded-full bg-surface-hover overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${h.confidence_pct}%`, backgroundColor: color }} />
      </div>
    </div>
  );
}

const nodeTypes = {
  incident: IncidentNodeCard,
  evidence: EvidenceNodeCard,
  hypothesis: HypothesisNodeCard,
};

const NODE_SIZE: Record<GraphNode["kind"], { width: number; height: number }> = {
  incident: { width: 224, height: 76 },
  evidence: { width: 240, height: 92 },
  hypothesis: { width: 256, height: 104 },
};

function layout(nodes: GraphNode[], edges: GraphEdge[]): { flowNodes: Node[]; flowEdges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 24, ranksep: 96 });
  g.setDefaultEdgeLabel(() => ({}));

  for (const n of nodes) {
    const size = NODE_SIZE[n.kind];
    g.setNode(n.id, size);
  }
  for (const e of edges) {
    g.setEdge(e.source, e.target);
  }
  dagre.layout(g);

  const flowNodes: Node[] = nodes.map((n) => {
    const pos = g.node(n.id);
    const size = NODE_SIZE[n.kind];
    return {
      id: n.id,
      type: n.kind,
      position: { x: pos.x - size.width / 2, y: pos.y - size.height / 2 },
      data: n,
      selectable: true,
    };
  });

  const EDGE_COLOR: Record<GraphEdge["kind"], string> = {
    gathered: "var(--border-strong)",
    supports: "var(--ok)",
    contradicts: "var(--danger)",
  };

  const flowEdges: Edge[] = edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    animated: e.kind !== "gathered",
    style: {
      stroke: EDGE_COLOR[e.kind],
      strokeWidth: e.kind === "gathered" ? 1 : 1.5,
      strokeDasharray: e.kind === "contradicts" ? "4 3" : undefined,
    },
  }));

  return { flowNodes, flowEdges };
}

export function InvestigationGraph({
  incident,
  evidence,
  hypotheses,
  selectedId,
  onSelect,
}: {
  incident: IncidentTrigger;
  evidence: InvestigationEvidenceResponse | null;
  hypotheses: Hypothesis[];
  selectedId: string | null;
  onSelect: (id: string, kind: GraphNode["kind"]) => void;
}) {
  const { flowNodes, flowEdges } = useMemo(() => {
    const { nodes, edges } = deriveInvestigationGraph(incident, evidence, hypotheses);
    const { flowNodes, flowEdges } = layout(nodes, edges);
    return {
      flowNodes: flowNodes.map((n) => ({ ...n, selected: n.id === selectedId })),
      flowEdges,
    };
  }, [incident, evidence, hypotheses, selectedId]);

  return (
    <div className="h-full w-full">
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, node) => onSelect(node.id, (node.data as GraphNode).kind)}
        fitView
        fitViewOptions={{ padding: 0.3 }}
        proOptions={{ hideAttribution: true }}
        minZoom={0.3}
        nodesDraggable={false}
        nodesConnectable={false}
      >
        <Background variant={BackgroundVariant.Dots} color="var(--border)" gap={20} size={1} />
      </ReactFlow>
    </div>
  );
}
