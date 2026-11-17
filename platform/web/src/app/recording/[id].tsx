import React from "react";
import { Linking, Platform, StyleSheet, Text, View } from "react-native";
import { router, useLocalSearchParams } from "expo-router";
import { type AudioPlayer, createAudioPlayer } from "expo-audio";
import { clock, formatDate, formatDuration, isWorking, stateLabel } from "@/application/labels";
import { canManage, useSession } from "@/application/session";
import { useCachedState } from "@/application/useCachedState";
import { saveTextFiles } from "@/application/syncRecording";
import { ScreenSection } from "@/components/primitives";
import { Button, Card, Loading, Notice, Page, uiText } from "@/components/ui";
import { ApiError } from "@/infrastructure/api/client";
import type { RecordingDetailOut } from "@/infrastructure/api/types";
import { KEEPS_FILES, localFiles } from "@/infrastructure/files/localFiles";
import { tokens } from "@/theme/tokens";

export default function Recording() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { api, user } = useSession();
  const [recording, setRecording] = useCachedState<RecordingDetailOut | null>(`recording:${id}`, null, { persist: false });
  const [summary, setSummary] = useCachedState<string | null | "not_ready">(`recording:${id}:summary`, null, {
    persist: false,
  });
  const [error, setError] = React.useState<string | null>(null);
  const [note, setNote] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const player = React.useRef<AudioPlayer | null>(null);
  React.useEffect(() => () => player.current?.remove(), []);

  const load = React.useCallback(async () => {
    try {
      const detail = await api.getRecording(id);
      setRecording(detail);
      if (detail.transcript) {
        try {
          setSummary(await api.textFile(id, "summary"));
        } catch (e) {
          if (e instanceof ApiError && e.status === 404) setSummary("not_ready");
          else throw e;
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load this recording.");
    }
  }, [api, id, setRecording, setSummary]);

  React.useEffect(() => {
    void load();
  }, [load]);

  // While the server is still working on it, look again every few seconds.
  React.useEffect(() => {
    if (!recording || !isWorking(recording.state)) return;
    const timer = setInterval(() => void load(), 4000);
    return () => clearInterval(timer);
  }, [recording, load]);

  const act = async (name: string, work: () => Promise<void>) => {
    setBusy(name);
    setNote(null);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That did not work.");
    } finally {
      setBusy(null);
    }
  };

  if (!recording) {
    return (
      <Page title="Recording" back>
        {error ? <Notice tone="error">{error}</Notice> : <Loading />}
      </Page>
    );
  }

  const play = () =>
    act("play", async () => {
      if (Platform.OS === "web") {
        await Linking.openURL(api.audioUrl(recording.id));
        return;
      }
      const local = `ASK/${recording.id}/audio.wav`;
      if (!(await localFiles.read(local))) await localFiles.write(local, await api.audioBytes(recording.id));
      const { File, Paths } = await import("expo-file-system");
      player.current?.remove();
      player.current = createAudioPlayer({ uri: new File(Paths.document, "ASK", recording.id, "audio.wav").uri });
      player.current.play();
    });

  const save = () =>
    act("save", async () => {
      const result = await saveTextFiles({ api, files: localFiles, recordingId: recording.id });
      const where = KEEPS_FILES ? "Saved on this phone" : "Downloaded";
      if (!result.transcript) setNote("There is no transcript yet. Try again when it is ready.");
      else if (result.summary === "not_ready") setNote(`${where}: transcription.txt. The summary is still being written.`);
      else setNote(`${where}: transcription.txt and summary.txt.`);
    });

  const manager = canManage(user);
  const segments = recording.transcript?.segments ?? [];
  const numbers = new Map<string, number>();
  segments.forEach((s) => numbers.has(s.speakerLabel) || numbers.set(s.speakerLabel, numbers.size + 1));

  return (
    <Page title={formatDate(recording.startedAt)} subtitle={`${recording.deviceName} · ${formatDuration(recording.durationMs)}`} back>
      <Text style={[uiText.secondary, { color: recording.state === "failed" ? tokens.colors.error : tokens.colors.textSecondary }]}>
        {stateLabel(recording.state)}
      </Text>
      {recording.failureDetail ? <Notice tone="error">{recording.failureDetail}</Notice> : null}
      {recording.transcript?.placeholder ? (
        <Notice>Placeholder text from the test setup, not a real transcript.</Notice>
      ) : null}
      {error ? <Notice tone="error">{error}</Notice> : null}
      {note ? <Notice tone="success">{note}</Notice> : null}

      <Button label="Play audio" kind="secondary" onPress={play} loading={busy === "play"} />
      {recording.transcript ? <Button label="Save transcript and summary" onPress={save} loading={busy === "save"} /> : null}

      <ScreenSection>Summary</ScreenSection>
      {summary === "not_ready" || (!summary && recording.transcript) ? (
        <Text style={uiText.secondary}>The summary is still being written, or could not be made for this recording.</Text>
      ) : null}
      {!recording.transcript ? <Text style={uiText.secondary}>The summary comes after the transcript.</Text> : null}
      {summary && summary !== "not_ready" ? (
        <Card>
          <Text style={uiText.body}>{summary}</Text>
        </Card>
      ) : null}

      <ScreenSection>Transcript</ScreenSection>
      {!recording.transcript ? <Text style={uiText.secondary}>Not ready yet. This page updates by itself.</Text> : null}
      {recording.transcript && segments.length === 0 ? <Text style={uiText.secondary}>No speech was found in this recording.</Text> : null}
      {segments.map((s) => (
        <View key={s.id} style={styles.line}>
          <Text style={styles.who}>
            {s.person?.name ?? `Speaker ${numbers.get(s.speakerLabel)}`} · {clock(s.startMs)}
          </Text>
          <Text style={uiText.body}>{s.text}</Text>
        </View>
      ))}

      {manager ? (
        <>
          <ScreenSection>Manage</ScreenSection>
          <Button
            label="Process again"
            kind="secondary"
            loading={busy === "reprocess"}
            onPress={() =>
              act("reprocess", async () => {
                await api.reprocess(recording.id);
                setSummary(null);
                await load();
              })
            }
          />
          <Button
            label={confirmDelete ? "Tap again to delete for good" : "Delete recording"}
            kind="danger"
            loading={busy === "delete"}
            onPress={() => {
              if (!confirmDelete) {
                setConfirmDelete(true);
                return;
              }
              void act("delete", async () => {
                await api.deleteRecording(recording.id);
                router.replace("/activity");
              });
            }}
          />
        </>
      ) : null}
    </Page>
  );
}

const styles = StyleSheet.create({
  line: { paddingVertical: tokens.spacing.sm, borderBottomWidth: 1, borderBottomColor: tokens.colors.border },
  who: { fontSize: 12, color: tokens.colors.textMuted, marginBottom: 4 },
});
