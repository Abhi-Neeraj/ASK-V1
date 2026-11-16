import React from "react";
import { Text } from "react-native";
import { useFocusEffect } from "expo-router";
import { useSession } from "@/application/session";
import { RecordingRow } from "@/components/RecordingRow";
import { Button, Loading, Notice, Page, uiText } from "@/components/ui";
import type { RecordingOut } from "@/infrastructure/api/types";

export default function Recordings() {
  const { api } = useSession();
  const [items, setItems] = React.useState<RecordingOut[] | null>(null);
  const [total, setTotal] = React.useState(0);
  const [page, setPage] = React.useState(1);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(
    async (next: number) => {
      setBusy(true);
      setError(null);
      try {
        const result = await api.listRecordings({ page: next });
        setItems((current) => (next === 1 || !current ? result.items : [...current, ...result.items]));
        setTotal(result.total);
        setPage(next);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not load recordings.");
      } finally {
        setBusy(false);
      }
    },
    [api],
  );

  useFocusEffect(
    React.useCallback(() => {
      void load(1);
    }, [load]),
  );

  return (
    <Page title="Activity" subtitle="Every recording from your locket, newest first." onRefresh={() => load(1)} refreshing={busy && page === 1}>
      {error ? <Notice tone="error">{error}</Notice> : null}
      {items === null ? <Loading /> : null}
      {items?.length === 0 ? <Text style={uiText.secondary}>No recordings yet.</Text> : null}
      {items?.map((r) => (
        <RecordingRow key={r.id} recording={r} />
      ))}
      {items && items.length < total ? (
        <Button label="Show more" kind="secondary" loading={busy} onPress={() => load(page + 1)} />
      ) : null}
    </Page>
  );
}
