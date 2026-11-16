import React from "react";
import { StyleSheet, Text, View } from "react-native";
import { router } from "expo-router";
import { formatDate, formatDuration, stateLabel } from "@/application/labels";
import { Card } from "@/components/ui";
import type { RecordingOut } from "@/infrastructure/api/types";
import { tokens } from "@/theme/tokens";

export function RecordingRow({ recording }: { recording: RecordingOut }) {
  const ready = recording.state === "completed" || recording.state === "needs_review";
  return (
    <Card label={`Open recording from ${formatDate(recording.startedAt)}`} onPress={() => router.push(`/recording/${recording.id}`)}>
      <View style={styles.top}>
        <Text style={styles.when}>{formatDate(recording.startedAt)}</Text>
        <Text style={[styles.state, { color: ready ? tokens.colors.success : recording.state === "failed" ? tokens.colors.error : tokens.colors.textSecondary }]}>
          {stateLabel(recording.state)}
        </Text>
      </View>
      <Text style={styles.meta}>
        {recording.deviceName} · {formatDuration(recording.durationMs)}
        {recording.placeholder ? " · placeholder text" : ""}
      </Text>
      {recording.snippet ? (
        <Text style={styles.snippet} numberOfLines={2}>
          {recording.snippet}
        </Text>
      ) : null}
    </Card>
  );
}

const styles = StyleSheet.create({
  top: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  when: { fontSize: 16, fontWeight: "600", color: tokens.colors.textPrimary },
  state: { fontSize: 13 },
  meta: { fontSize: 13, color: tokens.colors.textMuted, marginTop: 4 },
  snippet: { fontSize: 14, color: tokens.colors.textSecondary, marginTop: 8, lineHeight: 20 },
});
