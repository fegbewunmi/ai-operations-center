"use client";

import { useState } from "react";
import { submitHypothesisFeedback, ApiError } from "@/lib/api";
import type { FeedbackVerdict, Hypothesis, HypothesisFeedback } from "@/lib/types";
import { AuthorityBadge, CategoryDot, ConfidenceBar, EmptyState, Panel } from "@/components/ui";

export function HypothesisPanel({
  investigationId,
  hypotheses,
  feedback,
  onFeedbackSubmitted,
  selectedId,
  onSelect,
}: {
  investigationId: string;
  hypotheses: Hypothesis[];
  feedback: HypothesisFeedback[];
  onFeedbackSubmitted: () => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <Panel title="Hypotheses" className="flex-1 min-h-0">
      <div className="h-full overflow-auto p-2 flex flex-col gap-2">
        {hypotheses.length === 0 ? (
          <EmptyState>No hypotheses yet — waiting on evidence.</EmptyState>
        ) : (
          hypotheses
            .slice()
            .sort((a, b) => b.confidence_pct - a.confidence_pct)
            .map((h) => (
              <HypothesisCard
                key={h.hypothesis_id}
                investigationId={investigationId}
                hypothesis={h}
                latestFeedback={[...feedback].reverse().find((f) => f.hypothesis_id === h.hypothesis_id) ?? null}
                onFeedbackSubmitted={onFeedbackSubmitted}
                selected={selectedId === h.hypothesis_id}
                onSelect={() => onSelect(h.hypothesis_id)}
              />
            ))
        )}
      </div>
    </Panel>
  );
}

function HypothesisCard({
  investigationId,
  hypothesis,
  latestFeedback,
  onFeedbackSubmitted,
  selected,
  onSelect,
}: {
  investigationId: string;
  hypothesis: Hypothesis;
  latestFeedback: HypothesisFeedback | null;
  onFeedbackSubmitted: () => void;
  selected: boolean;
  onSelect: () => void;
}) {
  const [submitting, setSubmitting] = useState<FeedbackVerdict | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [showChallenge, setShowChallenge] = useState(false);

  async function submit(verdict: FeedbackVerdict) {
    setSubmitting(verdict);
    setError(null);
    try {
      await submitHypothesisFeedback(investigationId, hypothesis.hypothesis_id, {
        verdict,
        note: verdict === "challenged" ? note || undefined : undefined,
        submitted_by: "operator",
      });
      setShowChallenge(false);
      setNote("");
      onFeedbackSubmitted();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to submit feedback");
    } finally {
      setSubmitting(null);
    }
  }

  return (
    <div
      onClick={onSelect}
      className={`cursor-pointer rounded-md border p-3 transition-colors ${
        selected ? "border-accent bg-accent/5" : "border-border bg-surface-raised hover:border-border-strong"
      }`}
    >
      <div className="flex items-center gap-2 mb-1.5">
        <CategoryDot category={hypothesis.root_cause_category} />
        <span className="text-[11px] uppercase tracking-wide text-fg-faint">{hypothesis.root_cause_category}</span>
        <AuthorityBadge level={hypothesis.authority_level} />
        {latestFeedback && (
          <span
            className={`ml-auto text-[10px] font-medium uppercase tracking-wide ${
              latestFeedback.verdict === "accepted"
                ? "text-ok"
                : latestFeedback.verdict === "rejected"
                  ? "text-danger"
                  : "text-warn"
            }`}
          >
            {latestFeedback.verdict}
          </span>
        )}
      </div>

      <p className="text-[12px] text-fg leading-snug mb-2">{hypothesis.description}</p>
      <ConfidenceBar pct={hypothesis.confidence_pct} />

      {hypothesis.supporting_evidence.length > 0 && (
        <div className="mt-2">
          <p className="text-[10px] uppercase tracking-wide text-fg-faint mb-0.5">Supporting</p>
          <ul className="list-disc list-inside space-y-0.5">
            {hypothesis.supporting_evidence.map((e, i) => (
              <li key={i} className="text-[11px] text-fg-muted leading-snug">
                {e}
              </li>
            ))}
          </ul>
        </div>
      )}

      {hypothesis.contradicting_evidence.length > 0 && (
        <div className="mt-2">
          <p className="text-[10px] uppercase tracking-wide text-fg-faint mb-0.5">Contradicting</p>
          <ul className="list-disc list-inside space-y-0.5">
            {hypothesis.contradicting_evidence.map((e, i) => (
              <li key={i} className="text-[11px] text-danger/80 leading-snug">
                {e}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="mt-2 text-[11px] text-fg-muted">
        <span className="text-fg-faint">Recommended: </span>
        {hypothesis.recommended_action}
      </p>

      <div className="mt-2 flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
        <button
          onClick={() => submit("accepted")}
          disabled={submitting !== null}
          className="rounded border border-ok/30 bg-ok/10 px-2 py-1 text-[10px] font-medium text-ok hover:bg-ok/20 disabled:opacity-50"
        >
          Accept
        </button>
        <button
          onClick={() => submit("rejected")}
          disabled={submitting !== null}
          className="rounded border border-danger/30 bg-danger/10 px-2 py-1 text-[10px] font-medium text-danger hover:bg-danger/20 disabled:opacity-50"
        >
          Reject
        </button>
        <button
          onClick={() => setShowChallenge((s) => !s)}
          disabled={submitting !== null}
          className="rounded border border-warn/30 bg-warn/10 px-2 py-1 text-[10px] font-medium text-warn hover:bg-warn/20 disabled:opacity-50"
        >
          Challenge
        </button>
      </div>

      {showChallenge && (
        <div className="mt-2 flex flex-col gap-1.5" onClick={(e) => e.stopPropagation()}>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Why are you challenging this? Investigation will reopen with this note."
            rows={2}
            className="w-full resize-none rounded border border-border bg-bg px-2 py-1 text-[11px] text-fg placeholder:text-fg-faint focus:border-accent focus:outline-none"
          />
          <button
            onClick={() => submit("challenged")}
            disabled={submitting !== null}
            className="self-start rounded bg-warn px-2 py-1 text-[10px] font-medium text-black hover:opacity-90 disabled:opacity-50"
          >
            {submitting === "challenged" ? "Reopening…" : "Reopen investigation"}
          </button>
        </div>
      )}

      {error && <p className="mt-1.5 text-[10px] text-danger">{error}</p>}
    </div>
  );
}
