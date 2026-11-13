"use client";
import { useState } from "react";
import { TrainingPlanOverview } from "./training-plan-overview";
import { ManagementProfileEditor } from "./management-profile-editor";
import type { ManagementProfileResponse, ManagementOutlook } from "../lib/training-management";
import type { PlanningProfile, MesocycleAccentPreferencesResponse, PlanningMethodology } from "../lib/planning-profile";
import type { PlanningCalendarResponse } from "../lib/planning-calendar";
export function PlanningProfileForm({profile,managementProfile,accentPreferences,planningCalendar,notice,athleteAlias,outlook}: {
 outlook?:ManagementOutlook|null; profile:PlanningProfile|null; managementProfile?:ManagementProfileResponse; methodology?:PlanningMethodology; athleteAlias?:string;
 accentPreferences?:MesocycleAccentPreferencesResponse; planningCalendar:PlanningCalendarResponse; notice?:string;
}) {
 const [currentOutlook,setCurrentOutlook]=useState(outlook);
 const [observedOutlook,setObservedOutlook]=useState(outlook);
 const [pending,setPending]=useState(false);
 const [observedAthlete,setObservedAthlete]=useState(athleteAlias);
 if(outlook!==observedOutlook||athleteAlias!==observedAthlete){setObservedOutlook(outlook);setObservedAthlete(athleteAlias);setCurrentOutlook(outlook);if(athleteAlias!==observedAthlete)setPending(false);}
 return <main className="activities-page management-page planning-page"><p className="eyebrow">Управление на подготовката</p><h1>Профил за планиране</h1><p>Тук задаваш целите, дните, акцентите и методите. Седмичната програма и дългосрочният план са отделни изгледи в менюто.</p>
 {notice&&<p className="management-notice">{notice}</p>}
 <ManagementProfileEditor key={athleteAlias} initialProfile={managementProfile??{configured:false,profile:null,revision:0}} today={managementProfile?.today??new Date().toISOString().slice(0,10)} legacy={profile} legacyAccents={accentPreferences} calendar={planningCalendar} outlook={currentOutlook} onOutlookChange={setCurrentOutlook} onPlanningPending={setPending}/>
 {!pending&&currentOutlook&&<TrainingPlanOverview plan={currentOutlook} outcomes={[]} today={managementProfile?.today??currentOutlook.generated_at.slice(0,10)} currentProfileRevision={currentOutlook.profile_revision} volumeContext={currentOutlook.volume_context}/>}</main>;
}
