"use client";
import { useState } from "react";
import { TrainingPlanSummary } from "./training-plan-summary";
import type { PlanningDraft } from "../lib/training-management";
import type { QComparisonOptions } from "../lib/plan-q-comparison";

export function TrainingPlanReport({plan, proposal, showProposal: controlledProposal, onShowProposalChange, ...options}: {plan: PlanningDraft; proposal?: PlanningDraft | null; showProposal?: boolean; onShowProposalChange?: (show: boolean) => void} & QComparisonOptions) {
  const [localProposal, setLocalProposal] = useState(false);
  const showProposal = controlledProposal ?? localProposal;
  const setShowProposal = (show: boolean) => {setLocalProposal(show); onShowProposalChange?.(show);};
  const showingProposal = showProposal && !!proposal;
  return <>
    {proposal && <div className="management-panel"><p>Има ново предложение. Изборът сменя едновременно отчета и показаните тренировки.</p>
      <div className="management-actions" role="group" aria-label="Вариант за отчета">
        <button type="button" className="action-button secondary" aria-pressed={!showingProposal} onClick={() => setShowProposal(false)}>Утвърдена програма</button>
        <button type="button" className="action-button secondary" aria-pressed={showingProposal} onClick={() => setShowProposal(true)}>Ново предложение</button>
      </div>{showingProposal && <p>Преглеждаш предложението. То очаква утвърждаване; тренировките по-долу са от предложения вариант.</p>}
    </div>}
    <TrainingPlanSummary {...options} plan={showingProposal ? proposal! : plan} planKind={showingProposal ? "DRAFT" : options.planKind}/>
  </>;
}
