import { headers } from "next/headers";
import { notFound } from "next/navigation";
const views = {
  activities: "/activities", detail: "/activities/act_4fe746a9f2614cb29c6d2d4ab06c769f",
  shadow: "/activities/act_4fe746a9f2614cb29c6d2d4ab06c769f/shadow",
  load: "/?view=load", recovery: "/?view=recovery", index: "/trainability", speed: "/speed",
  response: "/response", planning: "/planning", management: "/management",
};
export default async function Review({ searchParams }: { searchParams: Promise<{view?: string}> }) {
  if (process.env.ONFLOWS_DATA_MODE !== "fixture" && (await headers()).get("host") !== "onflows-web-staging.onrender.com") notFound();
  const key = (await searchParams).view ?? "activities", src = views[key as keyof typeof views] ?? views.activities;
  return <main><p>Проверка на мобилното оформление</p><nav style={{display:"flex",flexWrap:"wrap",gap:12,marginBottom:16}}>{Object.keys(views).map(view=><a key={view} href={`?view=${view}`}>{view}</a>)}</nav><div style={{display:"flex",flexWrap:"wrap",gap:20}}>{[360,390].map(width=><div key={width} style={{width,flexShrink:0}}><p>{width} px</p><iframe title={`preview-${width}`} src={src} style={{width:"100%",height:750,border:"1px solid #b5c4cc",borderRadius:16}} /></div>)}</div></main>;
}
