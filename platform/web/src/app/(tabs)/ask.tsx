import React from "react";
import { Text } from "react-native";
import { useSession } from "@/application/session";
import { RecordingRow } from "@/components/RecordingRow";
import { Button, Field, Notice, Page, uiText } from "@/components/ui";
import type { RecordingOut } from "@/infrastructure/api/types";

export default function Ask() {
  const { api } = useSession();
  const [query, setQuery] = React.useState("");
  const [results, setResults] = React.useState<RecordingOut[] | null>(null);
  const [searched, setSearched] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const search = async () => {
    const q = query.trim();
    if (!q) return;
    setBusy(true);
    setError(null);
    try {
      const page = await api.listRecordings({ q, pageSize: 50 });
      setResults(page.items);
      setSearched(q);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Search did not work.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Page title="Ask" subtitle="Find what was said. Search every transcript.">
      <Field
        label="Words to look for"
        value={query}
        onChangeText={setQuery}
        placeholder="panel delivery, Friday, budget"
        returnKeyType="search"
        onSubmitEditing={search}
      />
      <Button label="Search" onPress={search} loading={busy} disabled={!query.trim()} />
      {error ? <Notice tone="error">{error}</Notice> : null}
      {results?.length === 0 ? <Text style={[uiText.secondary, { marginTop: 16 }]}>Nothing matches “{searched}”.</Text> : null}
      {results?.map((r) => (
        <RecordingRow key={r.id} recording={r} />
      ))}
    </Page>
  );
}
