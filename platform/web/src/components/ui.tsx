// Small building blocks for the server-backed screens, in the app's design system (src/theme/tokens.ts).
import React from "react";
import {
  ActivityIndicator,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  type TextInputProps,
  TouchableOpacity,
  View,
} from "react-native";
import { router } from "expo-router";
import { tokens } from "@/theme/tokens";

export function Page({
  title,
  subtitle,
  back,
  children,
  onRefresh,
  refreshing = false,
}: {
  title: string;
  subtitle?: string;
  back?: boolean;
  children: React.ReactNode;
  onRefresh?: () => void;
  refreshing?: boolean;
}) {
  return (
    <ScrollView
      style={styles.page}
      contentContainerStyle={styles.content}
      keyboardShouldPersistTaps="handled"
      refreshControl={onRefresh ? <RefreshControl refreshing={refreshing} onRefresh={onRefresh} /> : undefined}
    >
      <View style={styles.header}>
        {back ? (
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel="Back"
            onPress={() => (router.canGoBack() ? router.back() : router.replace("/home"))}
            style={styles.back}
          >
            <Text style={styles.backText}>‹  Back</Text>
          </TouchableOpacity>
        ) : (
          <Text style={styles.wordmark}>
            ask<Text style={styles.mark}>) )</Text>
          </Text>
        )}
      </View>
      <Text style={styles.title} accessibilityRole="header">
        {title}
      </Text>
      {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
      {children}
    </ScrollView>
  );
}

type ButtonKind = "primary" | "secondary" | "danger";

export function Button({
  label,
  onPress,
  kind = "primary",
  loading = false,
  disabled = false,
}: {
  label: string;
  onPress: () => void;
  kind?: ButtonKind;
  loading?: boolean;
  disabled?: boolean;
}) {
  const off = disabled || loading;
  return (
    <TouchableOpacity
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled: off, busy: loading }}
      disabled={off}
      onPress={onPress}
      style={[styles.button, styles[kind], off && styles.off]}
    >
      {loading ? (
        <ActivityIndicator color={tokens.colors.textPrimary} />
      ) : (
        <Text style={[styles.buttonText, kind === "danger" && { color: tokens.colors.error }]}>{label}</Text>
      )}
    </TouchableOpacity>
  );
}

export function Field({ label, ...input }: { label: string } & TextInputProps) {
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <TextInput
        accessibilityLabel={label}
        placeholderTextColor={tokens.colors.textMuted}
        autoCapitalize="none"
        style={styles.input}
        {...input}
      />
    </View>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "error" | "success"; children: React.ReactNode }) {
  const color = tone === "error" ? tokens.colors.error : tone === "success" ? tokens.colors.success : tokens.colors.textSecondary;
  return (
    <View style={[styles.notice, { borderColor: tone === "info" ? tokens.colors.border : color }]}>
      <Text style={[styles.noticeText, { color }]}>{children}</Text>
    </View>
  );
}

export function Card({ children, onPress, label }: { children: React.ReactNode; onPress?: () => void; label?: string }) {
  if (!onPress) return <View style={styles.card}>{children}</View>;
  return (
    <TouchableOpacity accessibilityRole="button" accessibilityLabel={label} onPress={onPress} style={styles.card}>
      {children}
    </TouchableOpacity>
  );
}

export function Loading() {
  return (
    <View style={styles.loading}>
      <ActivityIndicator color={tokens.colors.accent} />
    </View>
  );
}

export function Gap({ size = tokens.spacing.md }: { size?: number }) {
  return <View style={{ height: size }} />;
}

export const uiText = StyleSheet.create({
  strong: { fontSize: 16, fontWeight: "600", color: tokens.colors.textPrimary },
  body: { fontSize: 15, lineHeight: 22, color: tokens.colors.textPrimary },
  muted: { fontSize: 13, color: tokens.colors.textMuted },
  secondary: { fontSize: 14, color: tokens.colors.textSecondary },
  mono: { fontSize: 13, color: tokens.colors.textSecondary, fontFamily: "monospace" },
});

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: tokens.colors.bg },
  content: {
    paddingHorizontal: tokens.spacing.lg,
    paddingTop: tokens.spacing.xl,
    paddingBottom: 112,
    maxWidth: 640,
    width: "100%",
    alignSelf: "center",
  },
  header: { marginBottom: tokens.spacing.lg, minHeight: 28, justifyContent: "center" },
  back: { height: 40, justifyContent: "center", alignSelf: "flex-start" },
  backText: { fontSize: 16, color: tokens.colors.textSecondary },
  wordmark: { fontSize: 24, fontWeight: "700", color: tokens.colors.textPrimary },
  mark: { color: tokens.colors.accent, fontWeight: "700" },
  title: { fontSize: 34, fontWeight: "600", color: tokens.colors.textPrimary, letterSpacing: -0.5 },
  subtitle: { fontSize: 16, color: tokens.colors.textSecondary, marginTop: tokens.spacing.xs, marginBottom: tokens.spacing.sm },
  button: {
    minHeight: 48,
    borderRadius: tokens.radii.primaryButton,
    paddingHorizontal: tokens.spacing.lg,
    alignItems: "center",
    justifyContent: "center",
    marginTop: tokens.spacing.sm,
  },
  primary: { backgroundColor: tokens.colors.accent },
  secondary: { backgroundColor: tokens.colors.surfaceRaised, borderWidth: 1, borderColor: tokens.colors.border },
  danger: { backgroundColor: "transparent", borderWidth: 1, borderColor: tokens.colors.error },
  off: { opacity: 0.5 },
  buttonText: { fontSize: 16, fontWeight: "600", color: tokens.colors.textPrimary },
  field: { marginTop: tokens.spacing.md },
  fieldLabel: { fontSize: 13, color: tokens.colors.textSecondary, marginBottom: tokens.spacing.xxs },
  input: {
    minHeight: 48,
    borderRadius: tokens.radii.row,
    borderWidth: 1,
    borderColor: tokens.colors.border,
    backgroundColor: tokens.colors.surface,
    color: tokens.colors.textPrimary,
    paddingHorizontal: tokens.spacing.md,
    fontSize: 16,
  },
  notice: {
    marginTop: tokens.spacing.md,
    borderWidth: 1,
    borderRadius: tokens.radii.row,
    padding: tokens.spacing.md,
    backgroundColor: tokens.colors.surface,
  },
  noticeText: { fontSize: 14, lineHeight: 20 },
  card: {
    marginTop: tokens.spacing.sm,
    borderWidth: 1,
    borderColor: tokens.colors.border,
    borderRadius: tokens.radii.card,
    backgroundColor: tokens.colors.surface,
    padding: tokens.spacing.md,
  },
  loading: { paddingVertical: tokens.spacing.xl, alignItems: "center" },
});
