import type {
  DeploymentFindings,
  Hypothesis,
  IncidentTrigger,
  InvestigationEvidenceResponse,
  KnowledgeContext,
  TelemetryFindings,
} from "./types";

/**
 * Derives the investigation reasoning graph (incident -> evidence -> hypothesis)
 * from real state only. See the "Investigation Graph data model" section of the
 * approved plan for the full rationale — summarized inline below.
 */

export type EvidenceSpecialist = "telemetry" | "deployment" | "knowledge";

export interface IncidentGraphNode {
  kind: "incident";
  id: string;
  alertName: string;
  service: string;
  severity: string;
  onset: string;
}

export interface EvidenceGraphNode {
  kind: "evidence";
  id: string;
  specialist: EvidenceSpecialist;
  service: string;
  summary: string;
  badge: string;
  hasAnomaly: boolean;
  raw: TelemetryFindings | DeploymentFindings | KnowledgeContext;
}

export interface HypothesisGraphNode {
  kind: "hypothesis";
  id: string;
  hypothesis: Hypothesis;
}

export type GraphNode = IncidentGraphNode | EvidenceGraphNode | HypothesisGraphNode;

export type EdgeKind = "gathered" | "supports" | "contradicts";

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: EdgeKind;
}

const MIN_TOKEN_LENGTH = 4;

function identifiersFor(node: EvidenceGraphNode): string[] {
  switch (node.specialist) {
    case "telemetry": {
      const f = node.raw as TelemetryFindings;
      return f.anomalous_metrics
        .map((m) => m.name)
        .filter((name) => name.length >= MIN_TOKEN_LENGTH);
    }
    case "deployment": {
      const f = node.raw as DeploymentFindings;
      return f.deployments.flatMap((d) =>
        [d.version_to, d.deployment_id, d.git_commit_sha].filter(
          (t): t is string => !!t && t.length >= MIN_TOKEN_LENGTH,
        ),
      );
    }
    case "knowledge": {
      const k = node.raw as KnowledgeContext;
      return k.results.map((r) => r.title).filter((t) => t.length >= MIN_TOKEN_LENGTH);
    }
  }
}

function buildEvidenceNodes(evidence: InvestigationEvidenceResponse | null): EvidenceGraphNode[] {
  if (!evidence) return [];
  const nodes: EvidenceGraphNode[] = [];

  evidence.telemetry_findings.forEach((f, i) => {
    nodes.push({
      kind: "evidence",
      id: `telemetry-${i}`,
      specialist: "telemetry",
      service: f.service,
      summary: f.summary || "Telemetry query in progress.",
      badge: f.anomalous_metrics.length > 0 ? `${f.anomalous_metrics.length} anomal${f.anomalous_metrics.length === 1 ? "y" : "ies"}` : "no anomalies",
      hasAnomaly: f.anomalous_metrics.length > 0,
      raw: f,
    });
  });

  evidence.deployment_findings.forEach((f, i) => {
    nodes.push({
      kind: "evidence",
      id: `deployment-${i}`,
      specialist: "deployment",
      service: f.service,
      summary: f.summary || "Deployment query in progress.",
      badge: f.deployment_near_onset
        ? `near onset (${f.nearest_deployment_minutes?.toFixed(0)}m)`
        : `${f.deployments.length} deployment${f.deployments.length === 1 ? "" : "s"}`,
      hasAnomaly: f.deployment_near_onset,
      raw: f,
    });
  });

  evidence.knowledge_context.forEach((k, i) => {
    const topRelevance = k.results[0]?.relevance_score;
    nodes.push({
      kind: "evidence",
      id: `knowledge-${i}`,
      specialist: "knowledge",
      service: evidence.service_topology?.focal_service ?? "",
      summary: k.summary || "Knowledge search in progress.",
      badge: topRelevance !== undefined ? `top relevance ${topRelevance.toFixed(2)}` : `${k.results.length} docs`,
      hasAnomaly: false,
      raw: k,
    });
  });

  return nodes;
}

/**
 * Evidence -> hypothesis edges are inferred, not backend-modeled: Hypothesis.
 * supporting_evidence / contradicting_evidence are free-text prose written by the
 * Incident Analysis Agent, not references to specific evidence IDs. An edge is
 * drawn only when a real identifying token from the evidence (a deployment
 * version/id/commit, an anomalous metric name, a knowledge doc title) appears as a
 * substring inside the hypothesis's evidence-citation text. No match -> the
 * evidence node still renders, connected only to the incident, visibly showing
 * "gathered but not cited" rather than being hidden or force-connected.
 */
export function deriveInvestigationGraph(
  incident: IncidentTrigger,
  evidence: InvestigationEvidenceResponse | null,
  hypotheses: Hypothesis[],
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const incidentNode: IncidentGraphNode = {
    kind: "incident",
    id: "incident",
    alertName: incident.alert_name,
    service: incident.service_name,
    severity: incident.severity,
    onset: incident.onset_timestamp,
  };

  const evidenceNodes = buildEvidenceNodes(evidence);
  const hypothesisNodes: HypothesisGraphNode[] = hypotheses.map((h) => ({
    kind: "hypothesis",
    id: h.hypothesis_id,
    hypothesis: h,
  }));

  const edges: GraphEdge[] = evidenceNodes.map((n) => ({
    id: `incident->${n.id}`,
    source: "incident",
    target: n.id,
    kind: "gathered",
  }));

  for (const evidenceNode of evidenceNodes) {
    const tokens = identifiersFor(evidenceNode).map((t) => t.toLowerCase());
    if (tokens.length === 0) continue;

    for (const h of hypothesisNodes) {
      const supportingText = h.hypothesis.supporting_evidence.join(" ").toLowerCase();
      const contradictingText = h.hypothesis.contradicting_evidence.join(" ").toLowerCase();

      if (tokens.some((t) => supportingText.includes(t))) {
        edges.push({
          id: `${evidenceNode.id}->${h.id}:supports`,
          source: evidenceNode.id,
          target: h.id,
          kind: "supports",
        });
      }
      if (tokens.some((t) => contradictingText.includes(t))) {
        edges.push({
          id: `${evidenceNode.id}->${h.id}:contradicts`,
          source: evidenceNode.id,
          target: h.id,
          kind: "contradicts",
        });
      }
    }
  }

  return { nodes: [incidentNode, ...evidenceNodes, ...hypothesisNodes], edges };
}
