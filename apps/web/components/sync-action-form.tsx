import type { SyncScope } from "../lib/sync";

export function SyncActionForm({
  scope = "FULL",
  returnTo,
  busy = false,
  label = "Обнови данните",
  className = "action-button secondary",
}: {
  scope?: SyncScope;
  returnTo?: string;
  busy?: boolean;
  label?: string;
  className?: string;
}) {
  return <form action="/api/integrations/intervals/refresh" method="post">
    <input type="hidden" name="scope" value={scope} />
    {returnTo && <input type="hidden" name="returnTo" value={returnTo} />}
    <button className={className} type="submit" disabled={busy}>
      {busy ? "Обновяването е в ход" : label}
    </button>
  </form>;
}
