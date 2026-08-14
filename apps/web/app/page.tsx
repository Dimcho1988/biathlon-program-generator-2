import { getTrainingStatus } from "../lib/api";
import { Dashboard } from "../components/dashboard";
import { ErrorState } from "../components/error-state";

export default async function Page() {
  try {
    const result = await getTrainingStatus();
    return <Dashboard {...result} />;
  } catch (error) {
    const message = error instanceof Error ? error.message : "Възникна неочаквана грешка.";
    return <ErrorState message={message} />;
  }
}
