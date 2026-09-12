import Link from "next/link";
import {currentAuthorizedAthlete} from "../../lib/account-access";
import {getSpeedModel} from "../../lib/api";
import {ErrorState} from "../../components/error-state";
import {SpeedModelPanel} from "../../components/speed-model";
import "./speed.css";
export const dynamic="force-dynamic";
export default async function SpeedPage({searchParams}:{searchParams:Promise<{sport?:string;input?:string;value?:string;activity_ref?:string}>}){
  const access=await currentAuthorizedAthlete();
  if(!access)return <ErrorState message="Изберете достъпен профил на спортист." refreshAvailable={false}/>;
  if(!access.canViewRecovery)return <ErrorState message="Нямате достъп до физиологичните данни на този профил." refreshAvailable={false}/>;
  const q=await searchParams,query:Record<string,string>={};
  if(q.sport)query.sport=q.sport;
  const predictionInput=["minutes","km","speed"].includes(q.input||"")?q.input!:"minutes";
  const value=Number(q.value);
  const invalid=q.value!==undefined&&(!Number.isFinite(value)||value<=0||q.input!==predictionInput);
  if(q.value&&!invalid)query[predictionInput==="minutes"?"duration_s":predictionInput==="km"?"distance_m":"speed_kmh"]=String(value*(predictionInput==="minutes"?60:predictionInput==="km"?1000:1));
  let model;
  try{
    model=await getSpeedModel(access.athleteAlias,query);
    if(invalid)model={...model,prediction_error:"INVALID_PREDICTION_INPUT"};
  }catch(error){return <ErrorState message={error instanceof Error?error.message:"Скоростният модел временно не е достъпен."} retryAvailable retryHref="/speed"/>;}
  return <main className="activities-page speed-page"><nav className="detail-top-nav"><Link href="/">onFlows · Статус</Link><Link href="/activities">Активности</Link><Link href="/trainability">Индекс на тренираност</Link></nav><h1>Скорост, време и дистанция</h1><p>{access.displayName}</p><SpeedModelPanel key={`${access.athleteAlias}:${model.sport}`} model={model} canEdit={access.canEditPlan} activityRef={q.activity_ref} predictionInput={predictionInput} predictionValue={q.value??"3"}/></main>;
}
