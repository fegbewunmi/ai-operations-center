import type {
  EvalRun,
  FixtureScenario,
  InvestigationAnalysisResponse,
  InvestigationEvidenceResponse,
  InvestigationListItem,
  InvestigationStatusResponse,
  InvestigationTimelineResponse,
  StartInvestigationResponse,
  SynthesisOutput,
  FeedbackVerdict,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8080";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body || res.statusText);
  }
  return res.json() as Promise<T>;
}

// ---- Incident Library ----

export const listFixtures = () => request<FixtureScenario[]>("/v1/incidents/fixtures");

export const listInvestigations = () => request<InvestigationListItem[]>("/v1/investigations");

export const replayFixture = (fixtureId: string) =>
  request<StartInvestigationResponse>(`/v1/investigations/replay/${fixtureId}`, {
    method: "POST",
  });

// ---- Investigation Workspace ----

export const getStatus = (investigationId: string) =>
  request<InvestigationStatusResponse>(`/v1/investigations/${investigationId}/status`);

export const getEvidence = (investigationId: string) =>
  request<InvestigationEvidenceResponse>(`/v1/investigations/${investigationId}/evidence`);

export const getAnalysis = (investigationId: string) =>
  request<InvestigationAnalysisResponse>(`/v1/investigations/${investigationId}/analysis`);

export const getTimeline = (investigationId: string) =>
  request<InvestigationTimelineResponse>(`/v1/investigations/${investigationId}/timeline`);

export const getFindings = (investigationId: string) =>
  request<SynthesisOutput>(`/v1/investigations/${investigationId}/findings`);

export const submitApproval = (
  investigationId: string,
  body: { approved: boolean; approved_by: string; notes?: string },
) =>
  request<{ status: string; investigation_id: string }>(
    `/v1/investigations/${investigationId}/approval`,
    { method: "POST", body: JSON.stringify(body) },
  );

export const submitHypothesisFeedback = (
  investigationId: string,
  hypothesisId: string,
  body: { verdict: FeedbackVerdict; note?: string; submitted_by: string },
) =>
  request<{ status: string; investigation_id: string }>(
    `/v1/investigations/${investigationId}/hypotheses/${hypothesisId}/feedback`,
    { method: "POST", body: JSON.stringify(body) },
  );

// ---- Evaluation / Observability ----

export const listEvalRuns = () => request<EvalRun[]>("/v1/eval/runs");
