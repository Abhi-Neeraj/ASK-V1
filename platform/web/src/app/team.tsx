import React from "react";
import { Text, View } from "react-native";
import { useFocusEffect } from "expo-router";
import { formatDate } from "@/application/labels";
import { useSession } from "@/application/session";
import { ScreenSection } from "@/components/primitives";
import { Button, Card, Field, Loading, Notice, Page, uiText } from "@/components/ui";
import type { InvitationOut, MemberOut, Role } from "@/infrastructure/api/types";

const ROLE_NAME: Record<Role, string> = { admin: "Administrator", manager: "Manager", employee: "Member" };

export default function Team() {
  const { api, user } = useSession();
  const admin = user?.role === "admin";
  const [members, setMembers] = React.useState<MemberOut[] | null>(null);
  const [invitations, setInvitations] = React.useState<InvitationOut[]>([]);
  const [email, setEmail] = React.useState("");
  const [role, setRole] = React.useState<Role>("employee");
  const [error, setError] = React.useState<string | null>(null);
  const [note, setNote] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    try {
      const [people, invites] = await Promise.all([api.listMembers(), api.listInvitations()]);
      setMembers(people);
      setInvitations(invites.filter((i) => !i.accepted));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the team.");
    }
  }, [api]);

  useFocusEffect(
    React.useCallback(() => {
      void load();
    }, [load]),
  );

  const act = async (name: string, work: () => Promise<void>) => {
    setBusy(name);
    setError(null);
    setNote(null);
    try {
      await work();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That did not work.");
    } finally {
      setBusy(null);
    }
  };

  const roles: Role[] = admin ? ["employee", "manager", "admin"] : ["employee"];

  return (
    <Page title="Team" subtitle={user?.company.name} back>
      {error ? <Notice tone="error">{error}</Notice> : null}
      {note ? <Notice tone="success">{note}</Notice> : null}

      <ScreenSection>Invite someone</ScreenSection>
      <Field label="Email" value={email} onChangeText={setEmail} keyboardType="email-address" />
      <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
        {roles.map((r) => (
          <View key={r} style={{ flexGrow: 1 }}>
            <Button label={ROLE_NAME[r]} kind={role === r ? "primary" : "secondary"} onPress={() => setRole(r)} />
          </View>
        ))}
      </View>
      <Button
        label="Send invitation"
        loading={busy === "invite"}
        disabled={!email.trim()}
        onPress={() =>
          act("invite", async () => {
            await api.invite(email.trim(), role);
            setNote(`Invitation sent to ${email.trim()}.`);
            setEmail("");
          })
        }
      />

      {invitations.length ? <ScreenSection>Waiting to join</ScreenSection> : null}
      {invitations.map((i) => (
        <Card key={i.id}>
          <Text style={uiText.strong}>{i.email}</Text>
          <Text style={uiText.muted}>
            {ROLE_NAME[i.role]} · expires {formatDate(i.expiresAt)}
          </Text>
          <Button label="Cancel invitation" kind="danger" loading={busy === i.id} onPress={() => act(i.id, () => api.cancelInvitation(i.id))} />
        </Card>
      ))}

      <ScreenSection>People</ScreenSection>
      {members === null ? <Loading /> : null}
      {members?.map((m) => {
        const self = m.id === user?.id;
        return (
          <Card key={m.id}>
            <Text style={uiText.strong}>
              {m.name}
              {self ? " (you)" : ""}
            </Text>
            <Text style={uiText.secondary}>{m.email}</Text>
            <Text style={uiText.muted}>
              {ROLE_NAME[m.role]}
              {m.isActive ? "" : " · switched off"}
            </Text>
            {admin && !self ? (
              <View>
                {(["employee", "manager", "admin"] as Role[])
                  .filter((r) => r !== m.role)
                  .map((r) => (
                    <Button
                      key={r}
                      label={`Make ${ROLE_NAME[r].toLowerCase()}`}
                      kind="secondary"
                      loading={busy === `${m.id}-${r}`}
                      onPress={() => act(`${m.id}-${r}`, async () => void (await api.updateMember(m.id, { role: r })))}
                    />
                  ))}
                <Button
                  label={m.isActive ? "Switch off" : "Switch back on"}
                  kind={m.isActive ? "danger" : "secondary"}
                  loading={busy === `${m.id}-active`}
                  onPress={() => act(`${m.id}-active`, async () => void (await api.updateMember(m.id, { isActive: !m.isActive })))}
                />
              </View>
            ) : null}
          </Card>
        );
      })}
    </Page>
  );
}
