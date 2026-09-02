"use client";

import { useEffect, useState } from "react";
import { listFixtures, listInvestigations, ApiError } from "@/lib/api";
import type { FixtureScenario, InvestigationListItem } from "@/lib/types";
import { FixtureScenarioCard } from "@/components/incident-library/FixtureScenarioCard";
import { InvestigationHistoryTable } from "@/components/incident-library/InvestigationHistoryTable";
import { Panel } from "@/components/ui";

export default function IncidentLibraryPage() {
  const [fixtures, setFixtures] = useState<FixtureScenario[] | null>(null);
  const [history, setHistory] = useState<InvestigationListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listFixtures(), listInvestigations()])
      .then(([f, h]) => {
        if (cancelled) return;
        setFixtures(f);
        setHistory(h);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError
            ? `API error ${err.status}: ${err.message}`
            : "Could not reach the backend API. Is it running on " +
                (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8080") +
                "?",
        );
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6 sm:py-8 flex flex-col gap-8">
      <div>
        <h1 className="text-lg font-semibold text-fg">Incident Library</h1>
        <p className="mt-1 text-[13px] text-fg-muted">
          Launch a ground-truthed scenario or review past investigations.
        </p>
      </div>

      {error && (
        <div className="rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-[12px] text-danger">
          {error}
        </div>
      )}

      <section>
        <h2 className="mb-3 text-[12px] font-semibold uppercase tracking-wide text-fg-muted">
          Fixture scenarios
        </h2>
        {fixtures === null ? (
          <div className="text-[12px] text-fg-faint">Loading scenarios…</div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {fixtures.map((f) => (
              <FixtureScenarioCard key={f.fixture_id} fixture={f} />
            ))}
          </div>
        )}
      </section>

      <section className="flex-1 min-h-0 flex flex-col">
        <h2 className="mb-3 text-[12px] font-semibold uppercase tracking-wide text-fg-muted">
          Investigation history
        </h2>
        <Panel className="overflow-auto">
          {history === null ? (
            <div className="p-4 text-[12px] text-fg-faint">Loading history…</div>
          ) : (
            <InvestigationHistoryTable items={history} />
          )}
        </Panel>
      </section>
    </div>
  );
}
