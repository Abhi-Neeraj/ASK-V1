import React from "react";
import { Redirect, Stack, useSegments } from "expo-router";
import { MD3DarkTheme, PaperProvider } from "react-native-paper";
import { colors } from "@/theme/tokens";
import { SessionProvider, useSession } from "@/application/session";
import { LocketControllerProvider } from "@/application/locketController";
import { Loading } from "@/components/ui";

const theme = {
  ...MD3DarkTheme,
  roundness: 3,
  colors: {
    ...MD3DarkTheme.colors,
    primary: colors.accent,
    secondary: colors.success,
    background: colors.bg,
    surface: colors.surface,
    surfaceVariant: colors.surfaceRaised,
    onSurface: colors.textPrimary,
    onSurfaceVariant: colors.textSecondary,
    outline: colors.border,
    error: colors.error,
  },
};

const PUBLIC = new Set(["sign-in", "forgot-password", "reset-password"]);

// Signed out: only Sign in, Forgot password and Reset password. Signed in: never these.
function Gate() {
  const { status } = useSession();
  const segments = useSegments();
  const first = segments[0] ?? "";
  if (status === "loading") return <Loading />;
  if (status === "signedOut" && !PUBLIC.has(first)) return <Redirect href="/sign-in" />;
  if (status === "signedIn" && PUBLIC.has(first)) return <Redirect href="/home" />;
  return <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.bg } }} />;
}

export default function RootLayout() {
  return (
    <PaperProvider theme={theme}>
      <SessionProvider>
        <LocketControllerProvider>
          <Gate />
        </LocketControllerProvider>
      </SessionProvider>
    </PaperProvider>
  );
}
