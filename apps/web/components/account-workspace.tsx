import type { AccountRole, AccountWorkspace } from "../lib/account-access";
import { roleLabel } from "../lib/account-access";

const date = (value: string) => new Intl.DateTimeFormat("bg-BG", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
}).format(new Date(value));

const memberOrder: Record<AccountRole, number> = {
  ADMIN: 0,
  HEAD_COACH: 1,
  COACH: 2,
  ATHLETE: 3,
};

export function AccountWorkspacePanel({
  workspace,
  activeAthleteAlias,
  unlinkedAthleteAlias,
}: {
  workspace: AccountWorkspace;
  activeAthleteAlias?: string | null;
  unlinkedAthleteAlias?: string | null;
}) {
  const incomingInvites = workspace.invites.filter((invite) => invite.incoming);
  return <>
    <section className="role-section" aria-labelledby="role-profile-title">
      <div className="role-section-heading">
        <div><p className="section-kicker">Достъп</p><h2 id="role-profile-title">Моят работен профил</h2></div>
        <div className="role-badges" aria-label="Активни роли">
          {workspace.roles.length
            ? workspace.roles.map((role) => <span key={role}>{roleLabel(role)}</span>)
            : <span>Само акаунт</span>}
        </div>
      </div>
      <p>Един акаунт може едновременно да е личен профил на спортист и да има треньорска роля в отбор.</p>
    </section>

    {incomingInvites.length > 0 && <section className="role-section" aria-labelledby="invites-title">
      <div><p className="section-kicker">Покани</p><h2 id="invites-title">Чакащи покани</h2></div>
      <div className="role-stack">
        {incomingInvites.map((invite) => <article className="role-row" key={invite.id}>
          <div><strong>{invite.organizationName}</strong><small>{roleLabel(invite.role)} · валидна до {date(invite.expiresAt)}</small></div>
          <form method="post" action="/api/account/invites/accept">
            <input type="hidden" name="invite_id" value={invite.id} />
            <button className="action-button compact" type="submit" disabled={!workspace.displayName}>Приеми</button>
          </form>
        </article>)}
      </div>
      {!workspace.displayName && <small>Първо запази името на своя профил, след това приеми поканата.</small>}
    </section>}

    {unlinkedAthleteAlias && <section className="role-section role-callout" aria-labelledby="link-athlete-title">
      <div><p className="section-kicker">Intervals</p><h2 id="link-athlete-title">Свържи личния профил</h2></div>
      <p>Текущият защитен Intervals профил още не е свързан с този акаунт. След свързването той ще се появи като личен профил „Спортист“.</p>
      <form method="post" action="/api/account/athletes/link">
        <input type="hidden" name="athlete_alias" value={unlinkedAthleteAlias} />
        <button className="action-button" type="submit">Свържи текущия Intervals профил</button>
      </form>
    </section>}

    <section className="role-section" aria-labelledby="athletes-title">
      <div><p className="section-kicker">Спортисти</p><h2 id="athletes-title">Достъпни профили</h2></div>
      {workspace.accessibleAthletes.length ? <div className="role-stack">
        {workspace.accessibleAthletes.map((athlete) => <article className="role-row" key={athlete.athleteAlias}>
          <div><strong>{athlete.displayName}</strong><small>{athlete.isOwner ? "Личен профил" : athlete.canEditPlan ? "Възложен с право за промяна на плана" : "Достъп само за преглед"}</small></div>
          {activeAthleteAlias === athlete.athleteAlias
            ? <span className="active-profile">Активен</span>
            : <form method="post" action="/api/account/athletes/select">
              <input type="hidden" name="athlete_alias" value={athlete.athleteAlias} />
              <button className="action-button compact" type="submit">Отвори</button>
            </form>}
        </article>)}
      </div> : <p className="role-empty">Още няма свързан или възложен профил на спортист.</p>}
    </section>

    {workspace.memberships.map((membership) => {
      const members = workspace.members
        .filter((member) => member.organizationId === membership.organizationId)
        .sort((left, right) => memberOrder[left.role] - memberOrder[right.role] || left.displayName.localeCompare(right.displayName, "bg"));
      const coaches = members.filter((member) => member.status === "ACTIVE" && ["HEAD_COACH", "COACH"].includes(member.role));
      const athletes = members.filter((member) => member.status === "ACTIVE" && member.role === "ATHLETE");
      const canManage = ["ADMIN", "HEAD_COACH"].includes(membership.role);
      const pendingInvites = workspace.invites.filter((invite) => !invite.incoming && invite.organizationId === membership.organizationId);
      return <section className="role-section" aria-labelledby={`organization-${membership.organizationId}`} key={membership.organizationId}>
        <div className="role-section-heading">
          <div><p className="section-kicker">Отбор</p><h2 id={`organization-${membership.organizationId}`}>{membership.organizationName}</h2></div>
          <span className="organization-role">{roleLabel(membership.role)}</span>
        </div>
        <div className="member-grid">
          {members.map((member) => {
            const assignedCoaches = workspace.assignments
              .filter((assignment) => assignment.organizationId === membership.organizationId && assignment.athleteUserId === member.userId)
              .flatMap((assignment) => members.filter((candidate) => candidate.userId === assignment.coachUserId).map((coach) => coach.displayName));
            return <article key={member.userId}>
            <strong>{member.displayName}</strong>
            <span>{roleLabel(member.role)}</span>
            {assignedCoaches.length > 0 && <small>Треньор: {assignedCoaches.join(", ")}</small>}
            {member.status !== "ACTIVE" && <small>{member.status}</small>}
          </article>})}
        </div>
        {pendingInvites.length > 0 && <div className="pending-invites">
          <h3>Изпратени покани</h3>
          {pendingInvites.map((invite) => <p key={invite.id}><strong>{invite.inviteeEmail}</strong><span>{roleLabel(invite.role)} · до {date(invite.expiresAt)}</span></p>)}
        </div>}
        {canManage && <div className="role-management-grid">
          <form className="account-form role-form" method="post" action="/api/account/invites">
            <h3>Покани участник</h3>
            <input type="hidden" name="organization_id" value={membership.organizationId} />
            <label><span>Имейл</span><input name="invitee_email" type="email" maxLength={320} required /></label>
            <label><span>Роля</span><select name="membership_role" defaultValue="ATHLETE"><option value="ATHLETE">Спортист</option><option value="COACH">Треньор</option></select></label>
            <button className="action-button" type="submit">Създай покана</button>
            <small>Поканеният влиза със същия имейл и приема поканата от своя акаунт.</small>
          </form>
          <form className="account-form role-form" method="post" action="/api/account/assignments">
            <h3>Възложи спортист</h3>
            <input type="hidden" name="organization_id" value={membership.organizationId} />
            <label><span>Треньор</span><select name="coach_user_id" required>{coaches.map((coach) => <option value={coach.userId} key={coach.userId}>{coach.displayName}</option>)}</select></label>
            <label><span>Спортист</span><select name="athlete_user_id" required>{athletes.map((athlete) => <option value={athlete.userId} key={athlete.userId}>{athlete.displayName}</option>)}</select></label>
            <label className="checkbox-label"><input name="can_edit_plan" type="checkbox" defaultChecked /><span>Може да редактира плана</span></label>
            <button className="action-button" type="submit" disabled={!coaches.length || !athletes.length}>Запази възлагането</button>
            {(!coaches.length || !athletes.length) && <small>Необходими са активни профили на треньор и спортист.</small>}
          </form>
        </div>}
      </section>;
    })}

    <section className="role-section" aria-labelledby="create-team-title">
      <div><p className="section-kicker">Нова структура</p><h2 id="create-team-title">Създай отбор</h2></div>
      <p>Създателят получава роля „Главен треньор“ за новия отбор. Това не променя останалите му роли.</p>
      <form className="account-form" method="post" action="/api/account/organizations">
        <label><span>Име на отбора или организацията</span><input name="organization_name" minLength={1} maxLength={160} required /></label>
        <button className="action-button" type="submit" disabled={!workspace.displayName}>Създай отбора</button>
        {!workspace.displayName && <small>Първо запази името на своя профил.</small>}
      </form>
    </section>
  </>;
}
