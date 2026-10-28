"use client";
import { useState } from "react";
import { TrainingPlanSummary } from "./training-plan-summary";
import type { PlanningDraft } from "../lib/training-management";
import type { QComparisonOptions } from "../lib/plan-q-comparison";

export function TrainingPlanReport({plan, proposal, ...options}: {plan: PlanningDraft; proposal?: PlanningDraft | null} & QComparisonOptions) {
  const [showProposal, setShowProposal] = useState(false);
  const showingProposal = showProposal && !!proposal;
  return <>
    {proposal && <div className="management-panel"><p>Има ново предложение. Избери кой график да сравниш с целта и реалното изпълнение.</p>
      <div className="management-actions" role="group" aria-label="Вариант за отчета">
        <button type="button" className="action-button secondary" aria-pressed={!showingProposal} onClick={() => setShowProposal(false)}>Утвърдена програма</button>
        <button type="button" className="action-button secondary" aria-pressed={showingProposal} onClick={() => setShowProposal(true)}>Ново предложение</button>
      </div>{showingProposal && <p>Преглеждаш предложението. То очаква утвърждаване; седмичният график по-долу е текущата утвърдена програма.</p>}
    </div>}
    <TrainingPlanSummary {...options} plan={showingProposal ? proposal! : plan} planKind={showingProposal ? "DRAFT" : options.planKind}/>
  </>;
}
