import React from "react";
import { Text } from "react-native";
import { router } from "expo-router";
import { canManage, useSession } from "@/application/session";
import { Row, ScreenSection } from "@/components/primitives";
import { Button, Card, Page, uiText } from "@/components/ui";

const ROLE: Record<string, string> = { admin: "Administrator", manager: "Manager", employee: "Member" };

export default function You() {
  const { user, signOut } = useSession();
  const [busy, setBusy] = React.useState(false);

  return (
    <Page title="You" subtitle="Your account, your locket and your team.">
      <Card>
        <Text style={uiText.strong}>{user?.name}</Text>
        <Text style={uiText.secondary}>{user?.email}</Text>
        <Text style={uiText.muted}>
          {ROLE[user?.role ?? ""] ?? user?.role} · {user?.company.name}
        </Text>
      </Card>

      <ScreenSection>Locket</ScreenSection>
      <Row label="Lockets" second="Pair, people on it, firmware updates" onPress={() => router.push("/lockets")} />

      {canManage(user) ? (
        <>
          <ScreenSection>Team</ScreenSection>
          <Row label="Team" second="Invite people, change roles" onPress={() => router.push("/team")} />
        </>
      ) : null}

      <ScreenSection>Account</ScreenSection>
      <Row label="Change password" onPress={() => router.push("/account")} />
      <Button
        label="Sign out"
        kind="danger"
        loading={busy}
        onPress={async () => {
          setBusy(true);
          await signOut().finally(() => setBusy(false));
          router.replace("/sign-in");
        }}
      />
    </Page>
  );
}
