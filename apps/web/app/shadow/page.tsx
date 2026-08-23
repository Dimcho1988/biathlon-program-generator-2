import { redirect } from "next/navigation";

export default async function LegacyShadowPage({
  searchParams,
}: {
  searchParams: Promise<{ activity_ref?: string }>;
}) {
  const { activity_ref: activityRef } = await searchParams;
  if (activityRef && /^act_[a-f0-9]{32}$/.test(activityRef)) {
    redirect(`/activities/${activityRef}/shadow`);
  }
  redirect("/activities");
}
