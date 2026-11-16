import React from "react";
import { Text, View } from "react-native";
import { router, useFocusEffect } from "expo-router";
import { useLocketController } from "@/application/locketController";
import { useSession } from "@/application/session";
import { StatRow } from "@/components/primitives";
import { RecordingRow } from "@/components/RecordingRow";
import { Button, Card, Gap, Loading, Notice, Page, uiText } from "@/components/ui";
import type { LocketOut, RecordingOut, WorkspaceSummary } from "@/infrastructure/api/types";
import { ScreenSection } from "@/components/primitives";

export default function Today() {
  const { api, user } = useSession();
  const locket = useLocketController();
  const [lockets, setLockets] = React.useState<LocketOut[] | null>(null);
  const [recent, setRecent] = React.useState<RecordingOut[]>([]);
  const [summary, setSummary] = React.useState<WorkspaceSummary | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [refreshing, setRefreshing] = React.useState(false);

  const load = React.useCallback(async () => {
    setError(null);
    try {
      const [mine, page, counts] = await Promise.all([
        api.listLockets(),
        api.listRecordings({ pageSize: 5 }),
        api.workspaceSummary(),
      ]);
      setLockets(mine);
      setRecent(page.items);
      setSummary(counts);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load today.");
    }
  }, [api]);

  useFocusEffect(
    React.useCallback(() => {
      void load();
    }, [load]),
  );

  const refresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const recording = locket.status?.state === "recording";

  return (
    <Page title={`Hello, ${user?.name.split(" ")[0] ?? ""}`} subtitle="Your locket and your latest meetings." onRefresh={refresh} refreshing={refreshing}>
      {error ? <Notice tone="error">{error}</Notice> : null}
      {summary ? (
        <StatRow
          stats={[
            { value: summary.recordingsToday, label: "recordings today" },
            { value: summary.inProgress, label: "being processed" },
            { value: summary.needsReview + summary.failed, label: "need a look" },
          ]}
        />
      ) : null}

      <ScreenSection>Locket</ScreenSection>
      {lockets === null ? <Loading /> : null}
      {lockets?.length === 0 ? (
        <Card>
          <Text style={uiText.strong}>No locket yet</Text>
          <Text style={uiText.secondary}>Pair your locket to start recording meetings.</Text>
          <Button label="Set up a locket" onPress={() => router.push("/lockets")} />
        </Card>
      ) : null}
      {lockets?.map((item) => (
        <Card key={item.productId} label={`Open locket ${item.productId}`} onPress={() => router.push(`/lockets/${item.productId}`)}>
          <Text style={uiText.strong}>{item.name}</Text>
          <Text style={uiText.mono}>{item.productId}</Text>
          <Text style={uiText.muted}>
            {locket.connected && locket.productId === item.productId
              ? `Connected${locket.status ? ` · battery ${locket.status.batteryPercent}%` : ""}`
              : locket.reconnecting && locket.productId === item.productId
                ? "Connection lost, reconnecting…"
                : locket.available
                  ? "Not connected"
                  : `${item.members.length} of ${item.maxUsers} ${item.maxUsers === 1 ? "person" : "people"}`}
          </Text>
        </Card>
      ))}
      {locket.available && locket.connected ? (
        <View>
          <Button
            label={recording ? "Stop recording" : "Start recording"}
            kind={recording ? "danger" : "primary"}
            loading={locket.busy}
            onPress={recording ? locket.stopRecording : locket.startRecording}
          />
          {locket.message ? <Notice>{locket.message}</Notice> : null}
        </View>
      ) : null}
      {locket.available && !locket.connected && !locket.reconnecting && lockets?.length ? (
        <Button
          label={`Connect to ${lockets[0].productId}`}
          kind="secondary"
          loading={locket.busy}
          onPress={() => locket.connectTo(lockets[0].productId)}
        />
      ) : null}
      {locket.available && locket.message && !locket.connected ? <Notice>{locket.message}</Notice> : null}
      {locket.available && locket.pending > 0 ? (
        <Card>
          <Text style={uiText.strong}>
            {locket.pending} recording{locket.pending === 1 ? "" : "s"} waiting to upload
          </Text>
          <Text style={uiText.secondary}>They are saved on this phone and upload when you are online.</Text>
          <Button label="Upload now" kind="secondary" loading={locket.busy} onPress={locket.uploadWaiting} />
        </Card>
      ) : null}

      <ScreenSection>Latest recordings</ScreenSection>
      {recent.length === 0 && lockets !== null ? (
        <Text style={uiText.secondary}>Nothing recorded yet. Recordings appear here once the locket sends them.</Text>
      ) : null}
      {recent.map((r) => (
        <RecordingRow key={r.id} recording={r} />
      ))}
      {recent.length ? <Button label="See all recordings" kind="secondary" onPress={() => router.push("/activity")} /> : null}
      <Gap />
    </Page>
  );
}
