/**
 * Hand-mapped 1:1 to backend/app/shared/schemas/*.py and the API response shapes
 * in backend/app/api/v1/*.py. The backend has no OpenAPI-client-gen step, so these
 * are kept in sync by hand — do not add fields here that the backend doesn't emit.
 */

// ---- core.py ----

export interface TimeWindow {
  start: string;
  end: string;
}

export type TimelineEventType =
  | "alert"
  | "deployment"
  | "metric_anomaly"
  | "log_event"
  | "config_change"
  | "investigation_finding"
  | "synthesis"
  | "approval_requested"
  | "approval_granted"
  | "action_dispatched";

export interface TimelineEvent {
  timestamp: string;
  event_type: TimelineEventType;
  service: string;
  description: string;
  source: string;
  evidence_ref: string | null;
}

export type AgentErrorType = "tool_failure" | "llm_refusal" | "validation_error" | "timeout";

export interface AgentError {
  agent: string;
  query_summary: string;
  error_type: AgentErrorType;
  message: string;
  timestamp: string;
  retries_attempted: number;
}

// ---- incident.py ----

export interface IncidentTrigger {
  incident_id: string;
  alert_name: string;
  severity: string;
  service_name: string;
  onset_timestamp: string;
  description: string;
  alert_metadata: Record<string, unknown>;
}

export interface InvestigationBudget {
  max_iterations: number;
  max_tool_calls: number;
  max_tokens: number;
  max_latency_seconds: number;
  confidence_threshold: number;
  iterations_used: number;
  tool_calls_used: number;
  input_tokens_used: number;
  output_tokens_used: number;
  elapsed_seconds: number;
}

// ---- telemetry.py ----

export interface MetricPoint {
  name: string;
  value: number;
  unit: string;
  timestamp: string;
  is_anomalous: boolean;
  baseline_value: number | null;
}

export type LogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";

export interface LogEvent {
  timestamp: string;
  level: LogLevel;
  message: string;
  service: string;
  trace_id: string | null;
  count: number;
}

export interface ResourceUtilization {
  cpu_pct: number | null;
  memory_pct: number | null;
  connection_count: number | null;
  fd_count: number | null;
  thread_count: number | null;
}

export interface TelemetryFindings {
  service: string;
  time_window: TimeWindow;
  query: string;
  key_metrics: MetricPoint[];
  anomalous_metrics: MetricPoint[];
  log_events: LogEvent[];
  resource_utilization: ResourceUtilization;
  error_rate_change_pct: number | null;
  latency_p99_change_pct: number | null;
  summary: string;
  error: string | null;
}

// ---- deployment.py ----

export type DeploymentStatus = "success" | "failed" | "rolled_back" | "in_progress";

export interface DeploymentRecord {
  deployment_id: string;
  service: string;
  version_from: string | null;
  version_to: string;
  deployed_at: string;
  deployed_by: string;
  status: DeploymentStatus;
  config_changes: string[];
  rollback_available: boolean;
  rollback_target_version: string | null;
  git_commit_sha: string | null;
  minutes_before_onset: number | null;
}

export interface DeploymentFindings {
  service: string;
  time_window: TimeWindow;
  query: string;
  deployments: DeploymentRecord[];
  deployment_near_onset: boolean;
  nearest_deployment_minutes: number | null;
  summary: string;
  error: string | null;
}

// ---- knowledge.py ----

export type DocumentType = "runbook" | "postmortem" | "architecture_doc" | "error_pattern";

export interface KnowledgeResult {
  document_id: string;
  document_type: DocumentType;
  title: string;
  excerpt: string;
  relevance_score: number;
}

export interface SimilarIncident {
  incident_id: string;
  incident_type: string;
  affected_service: string;
  root_cause_description: string;
  remediation_applied: string | null;
  similarity_score: number;
  days_ago: number;
}

export interface ServiceOwnership {
  service: string;
  team: string;
  slack_channel: string;
  pagerduty_rotation: string | null;
  runbook_url: string | null;
  oncall_contact: string | null;
}

export type DependencyType = "synchronous" | "asynchronous" | "optional";

export interface ServiceNode {
  service: string;
  dependencies: string[];
  dependents: string[];
  dependency_types: Record<string, DependencyType>;
}

export interface ServiceTopology {
  focal_service: string;
  nodes: ServiceNode[];
  critical_path: string[];
  blast_radius: string[];
}

export interface KnowledgeContext {
  query: string;
  results: KnowledgeResult[];
  ownership: ServiceOwnership | null;
  similar_incidents: SimilarIncident[];
  summary: string;
  error: string | null;
}

// ---- synthesis.py ----

export type RootCauseCategory =
  | "deployment"
  | "dependency"
  | "resource"
  | "configuration"
  | "infrastructure"
  | "unknown";

export type AuthorityLevel = "L1" | "L2" | "L3";

export interface Hypothesis {
  hypothesis_id: string;
  description: string;
  root_cause_category: RootCauseCategory;
  affected_service: string;
  confidence_pct: number;
  supporting_evidence: string[];
  contradicting_evidence: string[];
  recommended_action: string;
  authority_level: AuthorityLevel;
}

export interface AnalysisOutput {
  hypotheses: Hypothesis[];
  top_hypothesis: Hypothesis;
  analysis_complete: boolean;
  requires_escalation: boolean;
  escalation_reason: string | null;
}

export interface SynthesisOutput {
  incident_id: string;
  investigation_summary: string;
  timeline: TimelineEvent[];
  hypotheses: Hypothesis[];
  top_hypothesis: Hypothesis;
  investigation_incomplete: boolean;
  requires_escalation: boolean;
  escalation_reason: string | null;
}

// ---- validation.py ----

export interface ValidationResult {
  passed: boolean;
  issues: string[];
  confidence_ok: boolean;
  evidence_grounded: boolean;
  sources_aligned: boolean;
  authority_ok: boolean;
  risk_level: AuthorityLevel;
  investigation_incomplete: boolean;
}

// ---- response.py ----

export interface DispatchedAction {
  action_type: "jira_ticket" | "slack_message" | "pagerduty_incident" | "pagerduty_update";
  external_id: string;
  url: string | null;
  dispatched_at: string;
  authority_level: "L1" | "L2" | "L3";
}

export interface PendingApproval {
  approval_id: string;
  investigation_id: string;
  action_description: string;
  authority_level: "L3";
  checkpoint_id: string;
  requested_at: string;
  approved: boolean | null;
  approved_by: string | null;
  approved_at: string | null;
  notes: string | null;
}

export type FeedbackVerdict = "accepted" | "rejected" | "challenged";

export interface HypothesisFeedback {
  hypothesis_id: string;
  verdict: FeedbackVerdict;
  note: string | null;
  submitted_by: string;
  submitted_at: string;
}

// ---- llm_tracking.py ----

export interface NodeTokenUsage {
  node: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

// ---- API response shapes (app/api/v1/investigations.py, eval.py, incidents.py) ----

export type InvestigationPhase =
  | "planning"
  | "investigating"
  | "synthesizing"
  | "responding"
  | "complete"
  | "escalated";

export interface StartInvestigationResponse {
  investigation_id: string;
  status: string;
  message: string;
}

export interface InvestigationStatusResponse {
  investigation_id: string;
  incident: IncidentTrigger;
  phase: InvestigationPhase;
  working_hypothesis: string | null;
  working_confidence: number;
  iterations_used: number;
  tool_calls_used: number;
  started_at: string;
  completed_at: string | null;
}

export interface InvestigationTimelineResponse {
  investigation_id: string;
  events: TimelineEvent[];
}

export interface InvestigationEvidenceResponse {
  investigation_id: string;
  telemetry_findings: TelemetryFindings[];
  deployment_findings: DeploymentFindings[];
  knowledge_context: KnowledgeContext[];
  service_topology: ServiceTopology | null;
}

export interface InvestigationAnalysisResponse {
  investigation_id: string;
  analysis_output: AnalysisOutput | null;
  validation_result: ValidationResult | null;
  token_log: NodeTokenUsage[];
  budget: InvestigationBudget;
  human_feedback: HypothesisFeedback[];
  pending_approvals: PendingApproval[];
  dispatched_actions: DispatchedAction[];
}

export interface InvestigationListItem {
  investigation_id: string;
  incident_id: string;
  alert_name: string;
  service_name: string;
  severity: string;
  phase: InvestigationPhase;
  started_at: string;
  completed_at: string | null;
  source: "live" | "replay";
  fixture_id: string | null;
}

export interface FixtureScenario {
  fixture_id: string;
  description: string;
  incident: {
    incident_id: string;
    alert_name: string;
    severity: string;
    service_name: string;
    onset_timestamp: string;
    description: string;
  };
  ground_truth: {
    root_cause_category: RootCauseCategory;
    affected_service: string;
    required_specialist_calls: string[];
    expected_authority_level: AuthorityLevel;
    expected_min_confidence: number;
    notes: string;
  };
}

export interface EvalScoreResult {
  incident_id: string;
  fixture_path: string;
  final_phase: string;
  accuracy: boolean;
  root_cause_category_correct: boolean;
  expected_root_cause_category: string;
  actual_root_cause_category: string | null;
  affected_service_correct: boolean;
  evidence_complete: boolean;
  required_specialists_called: boolean;
  final_confidence: number;
  mttfh_seconds: number;
  iterations_used: number;
  tool_calls_used: number;
  safety_guard_triggered: boolean;
  errors: string[];
}

export interface EvalRun {
  run_id: string;
  run_at: string;
  results: EvalScoreResult[];
}
