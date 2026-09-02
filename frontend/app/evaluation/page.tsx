"use client";

import { useEffect, useState } from "react";
import { listEvalRuns, ApiError } from "@/lib/api";
import type { EvalRun } from "@/lib/types";
import { EvalRunsTable } from "@/components/evaluation/EvalRunsTable";
import { EvalRunCompare } from "@/components/evaluation/EvalRunCompare";

export default function EvaluationPage() {
  const [runs, setRuns] = useState<EvalRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listEvalRuns()
      .then(setRuns)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not reach the backend API."));
  }, []);

  return (
    <div className="mx-auto w-full max-w-5xl flex-1 px-6 py-8 flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-semibold text-fg">Evaluation &amp; Observability</h1>
        <p className="mt-1 text-[13px] text-fg-muted">
          Exactly what <span className="mono">eval/scorer.py</span> computed for each committed run in{" "}
          <span className="mono">backend/eval_results/</span> - no metrics beyond that are shown here.
        </p>
      </div>

      {error && (
        <div className="rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-[12px] text-danger">
          {error}
        </div>
      )}

      {runs === null ? (
        <div className="text-[12px] text-fg-faint">Loading…</div>
      ) : runs.length === 0 ? (
        <div className="text-[12px] text-fg-faint">
          No eval runs committed yet. Run <span className="mono">python -m eval.run_eval</span> from{" "}
          <span className="mono">backend/</span>.
        </div>
      ) : (
        <>
          {runs.map((r) => (
            <EvalRunsTable key={r.run_id} run={r} />
          ))}
          <EvalRunCompare runs={runs} />
        </>
      )}
    </div>
  );
}
