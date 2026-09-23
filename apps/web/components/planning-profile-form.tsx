import { TrainingPlanOverview } from "./training-plan-overview";
import { ManagementProfileEditor } from "./management-profile-editor";
import type { ManagementProfileResponse, ManagementOutlook } from "../lib/training-management";
import type { PlanningProfile, MesocycleAccentPreferencesResponse, PlanningMethodology } from "../lib/planning-profile";
import { PlanningCalendarPanel } from "./planning-calendar-panel";
import type { PlanningCalendarResponse } from "../lib/planning-calendar";
export function PlanningProfileForm({profile,managementProfile,accentPreferences,planningCalendar,notice,athleteAlias,outlook}: {
 outlook?:ManagementOutlook|null; profile:PlanningProfile|null; managementProfile?:ManagementProfileResponse; methodology?:PlanningMethodology; athleteAlias?:string;
 accentPreferences?:MesocycleAccentPreferencesResponse; planningCalendar:PlanningCalendarResponse; notice?:string;
}) {
 return <main className="activities-page management-page planning-page"><p className="eyebrow">Управление на подготовката</p><h1>Профил за планиране</h1><p>Тук задаваш целите, дните, акцентите и методите. Седмичната програма и дългосрочният план са отделни изгледи в менюто.</p>
 {notice&&<p className="management-notice">{notice}</p>}
 <ManagementProfileEditor key={athleteAlias} initialProfile={managementProfile??{configured:false,profile:null,revision:0}} today={managementProfile?.today??new Date().toISOString().slice(0,10)} legacy={profile} legacyAccents={accentPreferences}/>
 <section className="management-panel" id="planning-calendar"><h2>Стартове и лагери · по желание</h2><p>Основните и контролните стартове определят календарния контекст. Лагерен акцент или стресова седмица се задават в стъпка 3.</p><PlanningCalendarPanel response={planningCalendar} today={managementProfile?.today??managementProfile?.profile?.program_start}/></section>{outlook&&<TrainingPlanOverview plan={outlook} outcomes={[]} today={managementProfile?.today??outlook.generated_at.slice(0,10)} currentProfileRevision={outlook.profile_revision} volumeContext={outlook.volume_context}/>}</main>;
}
