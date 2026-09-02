"use client";

import type { EvalRun } from "@/lib/types";
import { EmptyState, Panel } from "@/components/ui";

/**
 * Side-by-side comparison across committed runs, pivoted by fixture_id. With one
 * committed run (backend/eval_results/baseline.json) this renders one column —
 * real, not padded with placeholder runs. Populates further as more runs are
 * committed via `python -m eval.run_eval --output eval_results/<name>.json`.
 */
export function EvalRunCompare({ runs }: { runs: EvalRun[] }) {
  if (runs.length < 2) {
    return (
      <Panel title="Run comparison">
        <EmptyState>
          Only one eval run is committed ({runs[0]?.run_id ?? "none"}). Comparison needs at
          least two runs in backend/eval_results/.
        </EmptyState>
      </Panel>
    );
  }

  const fixtureIds = [...new Set(runs.flatMap((r) => r.results.map((res) => res.incident_id)))].sort();

  return (
    <Panel title="Run comparison">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="border-b border-border text-left text-fg-faint">
            <th className="px-3 py-2 font-medium">Fixture</th>
            {runs.map((r) => (
              <th key={r.run_id} className="px-3 py-2 font-medium">
                {r.run_id}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {fixtureIds.map((fid) => (
            <tr key={fid} className="border-b border-border last:border-0">
              <td className="px-3 py-1.5 font-mono text-fg">{fid}</td>
              {runs.map((r) => {
                const res = r.results.find((x) => x.incident_id === fid);
                if (!res) return <td key={r.run_id} className="px-3 py-1.5 text-fg-faint">-</td>;
                return (
                  <td key={r.run_id} className="px-3 py-1.5">
                    <span className={res.accuracy ? "text-ok" : "text-danger"}>
                      {res.final_confidence.toFixed(0)}%
                    </span>
                    <span className="ml-1.5 text-fg-faint">{res.mttfh_seconds.toFixed(0)}s</span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}
