import ManagementPage from "../page";
export const dynamic = "force-dynamic";
export default async function OutlookPage({searchParams}: {searchParams:Promise<{sync?:string}>}) {
  return ManagementPage({searchParams:searchParams.then(query=>({...query,view:"overview" as const}))});
}
