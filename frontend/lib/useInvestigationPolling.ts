"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getAnalysis, getEvidence, getStatus, getTimeline } from "./api";
import type {
  InvestigationAnalysisResponse,
  InvestigationEvidenceResponse,
  InvestigationStatusResponse,
  InvestigationTimelineResponse,
} from "./types";

const ACTIVE_PHASES = new Set(["planning", "investigating", "synthesizing", "responding"]);

const ACTIVE_POLL_MS = 1500;
const IDLE_POLL_MS = 5000; // still polls when complete/escalated, in case a "challenge" reopens it

export interface InvestigationPollState {
  status: InvestigationStatusResponse | null;
  evidence: InvestigationEvidenceResponse | null;
  analysis: InvestigationAnalysisResponse | null;
  timeline: InvestigationTimelineResponse | null;
  loading: boolean;
  error: string | null;
}

/**
 * Polls /status + /evidence + /analysis + /timeline for one investigation.
 * Polls at ACTIVE_POLL_MS while the investigation is actively running, and at the
 * slower IDLE_POLL_MS once complete/escalated (a "challenge" hypothesis action can
 * reopen it, so polling never fully stops while this component is mounted).
 */
export function useInvestigationPolling(investigationId: string | null): InvestigationPollState {
  const [state, setState] = useState<InvestigationPollState>({
    status: null,
    evidence: null,
    analysis: null,
    timeline: null,
    loading: true,
    error: null,
  });
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelledRef = useRef(false);

  const tick = useCallback(async () => {
    if (!investigationId || cancelledRef.current) return;
    try {
      const [status, evidence, analysis, timeline] = await Promise.all([
        getStatus(investigationId),
        getEvidence(investigationId),
        getAnalysis(investigationId),
        getTimeline(investigationId),
      ]);
      if (cancelledRef.current) return;
      setState({ status, evidence, analysis, timeline, loading: false, error: null });

      const delay = ACTIVE_PHASES.has(status.phase) ? ACTIVE_POLL_MS : IDLE_POLL_MS;
      timerRef.current = setTimeout(tick, delay);
    } catch (err) {
      if (cancelledRef.current) return;
      setState((prev) => ({
        ...prev,
        loading: false,
        error: err instanceof Error ? err.message : "Failed to poll investigation",
      }));
      timerRef.current = setTimeout(tick, IDLE_POLL_MS);
    }
  }, [investigationId]);

  useEffect(() => {
    cancelledRef.current = false;
    setState({ status: null, evidence: null, analysis: null, timeline: null, loading: true, error: null });
    tick();
    return () => {
      cancelledRef.current = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [tick]);

  return state;
}
