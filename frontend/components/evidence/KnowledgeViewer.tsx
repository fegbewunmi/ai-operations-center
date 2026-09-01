"use client";

import type { KnowledgeContext } from "@/lib/types";
import { EmptyState } from "@/components/ui";

const DOC_TYPE_LABEL: Record<string, string> = {
  runbook: "Runbook",
  postmortem: "Postmortem",
  architecture_doc: "Architecture",
  error_pattern: "Error pattern",
};

export function KnowledgeViewer({ context }: { context: KnowledgeContext }) {
  return (
    <div className="flex flex-col gap-3">
      {context.ownership && (
        <div className="rounded-md border border-border bg-surface-raised p-2.5 text-[11px]">
          <span className="text-fg-faint">Owned by </span>
          <span className="font-medium text-fg">{context.ownership.team}</span>
          <span className="text-fg-faint"> · </span>
          <span className="text-fg-muted">{context.ownership.slack_channel}</span>
          {context.ownership.runbook_url && (
            <>
              <span className="text-fg-faint"> · </span>
              <span className="mono text-fg-muted">{context.ownership.runbook_url}</span>
            </>
          )}
        </div>
      )}

      {context.results.length === 0 ? (
        <EmptyState>No matching documents found.</EmptyState>
      ) : (
        <div className="flex flex-col gap-2">
          {context.results.map((r) => (
            <div key={r.document_id} className="rounded-md border border-border bg-surface-raised p-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[10px] uppercase tracking-wide text-accent font-semibold">
                  {DOC_TYPE_LABEL[r.document_type] ?? r.document_type}
                </span>
                <span className="text-[10px] tabular-nums text-fg-faint">relevance {r.relevance_score.toFixed(2)}</span>
              </div>
              <h4 className="mt-0.5 text-[12px] font-medium text-fg">{r.title}</h4>
              <p className="mt-1 text-[11px] text-fg-muted leading-relaxed">{r.excerpt}</p>
            </div>
          ))}
        </div>
      )}

      {context.similar_incidents.length > 0 && (
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wide text-fg-faint">Similar past incidents</p>
          <div className="flex flex-col gap-1.5">
            {context.similar_incidents.map((s) => (
              <div key={s.incident_id} className="rounded border border-border px-2 py-1.5 text-[11px] text-fg-muted">
                {s.root_cause_description} — {s.days_ago}d ago (similarity {s.similarity_score.toFixed(2)})
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
