import React from "react";
import { Text, View } from "react-native";
import { router, useFocusEffect } from "expo-router";
import { useLocketController } from "@/application/locketController";
import { isWorking } from "@/application/labels";
import { useSession } from "@/application/session";
import { useCachedState } from "@/application/useCachedState";
import { StatRow } from "@/components/primitives";
import { RecordingRow } from "@/components/RecordingRow";
import { Button, Card, Gap, Loading, Notice, Page, uiText } from "@/components/ui";
import type { LocketOut, RecordingOut, WorkspaceSummary } from "@/infrastructure/api/types";
import { ScreenSection } from "@/components/primitives";

export default function Today() {
  const { api, user } = useSession();
  const locket = useLocketController();
  const [lockets, setLockets] = useCachedState<LocketOut[] | null>("lockets", null);
  const [recent, setRecent] = useCachedState<RecordingOut[]>("today:recent", []);
  const [summary, setSummary] = useCachedState<WorkspaceSummary | null>("today:summary", null);
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
  }, [api, setLockets, setRecent, setSummary]);

  useFocusEffect(
    React.useCallback(() => {
      void load();
    }, [load]),
  );

  // New recordings and their transcripts show up by themselves: while one is on its way or being processed, the
  // recent list is asked for again every 3 seconds (every 12 while a locket is just connected), never while the page
  // is hidden, and the screen is redrawn only when something changed.
  const working = recent.some((r) => isWorking(r.state));
  const loadRecent = React.useCallback(async () => {
    try {
      const page = await api.listRecordings({ pageSize: 5 });
      setRecent((current) => (JSON.stringify(current) === JSON.stringify(page.items) ? current : page.items));
    } catch {
      // a failed look is not shown as an error; the next one tries again
    }
  }, [api, setRecent]);
  React.useEffect(() => {
    const busy = working || locket.pending > 0;
    if (!busy && !locket.connected) return;
    const timer = setInterval(
      () => {
        if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
        void loadRecent();
      },
      busy ? 3000 : 12000,
    );
    return () => clearInterval(timer);
  }, [working, locket.pending, locket.connected, loadRecent]);

  const refresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const recording = locket.status?.state === "recording";
  // A recording is accepted only from someone paired with that locket: say so when the one connected is not theirs.
  const isMine = (item: LocketOut) => item.members.some((m) => m.id === user?.id);
  const mine = lockets?.filter(isMine) ?? [];

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
              ? `Connected${locket.status ? ` · battery ${locket.status.batteryPercent}%` : ""}${isMine(item) ? "" : " · not paired with you yet"}`
              : locket.reconnecting && locket.productId === item.productId
                ? "Connection lost, reconnecting…"
                : locket.available
                  ? isMine(item)
                    ? "Not connected"
                    : "Not paired with you yet"
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
        mine.length ? (
          <Button
            label={`Connect to ${mine[0].productId}`}
            kind="secondary"
            loading={locket.busy}
            onPress={() => locket.connectTo(mine[0].productId)}
          />
        ) : (
          <Button label="Pair a locket" kind="secondary" onPress={() => router.push("/lockets")} />
        )
      ) : null}
      {locket.available && locket.connected && locket.productId && !mine.some((l) => l.productId === locket.productId) ? (
        <View>
          <Notice>
            {`This account is not paired with ${locket.productId}, so its recordings cannot upload yet. Open Lockets, find it nearby and pair it.`}
          </Notice>
          <Button label="Pair it now" kind="secondary" onPress={() => router.push("/lockets")} />
        </View>
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
