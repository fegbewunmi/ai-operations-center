"use client";

import { useState } from "react";
import { submitApproval } from "@/lib/api";
import type { InvestigationAnalysisResponse } from "@/lib/types";
import { Panel } from "@/components/ui";

export function HumanControls({
  investigationId,
  analysis,
  onSubmitted,
}: {
  investigationId: string;
  analysis: InvestigationAnalysisResponse;
  onSubmitted: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);

  const pending = analysis.pending_approvals.filter((p) => p.approved === null);
  if (pending.length === 0) return null;

  async function respond(approved: boolean) {
    setSubmitting(true);
    setError(null);
    try {
      await submitApproval(investigationId, { approved, approved_by: "operator", notes: notes || undefined });
      onSubmitted();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit approval");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Panel title="L3 approval required" className="border-danger/40">
      <div className="p-3 flex flex-col gap-3">
        {pending.map((p) => (
          <p key={p.approval_id} className="text-[12px] text-fg leading-relaxed">
            {p.action_description}
          </p>
        ))}
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Notes (optional)"
          rows={2}
          className="w-full resize-none rounded border border-border bg-bg px-2 py-1.5 text-[12px] text-fg placeholder:text-fg-faint focus:border-accent focus:outline-none"
        />
        {error && <p className="text-[11px] text-danger">{error}</p>}
        <div className="flex gap-2">
          <button
            onClick={() => respond(true)}
            disabled={submitting}
            className="flex-1 rounded bg-ok px-3 py-1.5 text-[12px] font-medium text-black hover:opacity-90 disabled:opacity-50"
          >
            Approve
          </button>
          <button
            onClick={() => respond(false)}
            disabled={submitting}
            className="flex-1 rounded border border-danger/40 bg-danger/10 px-3 py-1.5 text-[12px] font-medium text-danger hover:bg-danger/20 disabled:opacity-50"
          >
            Reject
          </button>
        </div>
      </div>
    </Panel>
  );
}
