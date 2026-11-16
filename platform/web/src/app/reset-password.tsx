import React from "react";
import { router, useLocalSearchParams } from "expo-router";
import { useSession } from "@/application/session";
import { Button, Field, Notice, Page } from "@/components/ui";

// The page the emailed reset link opens: /reset-password?token=...
export default function ResetPassword() {
  const { api } = useSession();
  const { token } = useLocalSearchParams<{ token?: string }>();
  const [password, setPassword] = React.useState("");
  const [again, setAgain] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [done, setDone] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const mismatch = again.length > 0 && password !== again;

  const submit = async () => {
    if (!token || password !== again) return;
    setBusy(true);
    setError(null);
    try {
      await api.confirmPasswordReset(String(token), password);
      setDone(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That did not work. Ask for a new link.");
    } finally {
      setBusy(false);
    }
  };

  if (!token) {
    return (
      <Page title="Reset password" subtitle="This link is missing its code.">
        <Notice tone="error">Open the link from the reset email again, or ask for a new one.</Notice>
        <Button label="Ask for a new link" onPress={() => router.replace("/forgot-password")} />
      </Page>
    );
  }

  if (done) {
    return (
      <Page title="Password changed" subtitle="You can sign in with your new password.">
        <Notice tone="success">Your password has been changed.</Notice>
        <Button label="Go to sign in" onPress={() => router.replace("/sign-in")} />
      </Page>
    );
  }

  return (
    <Page title="Choose a new password" subtitle="Pick a password you do not use anywhere else.">
      <Field
        label="New password"
        value={password}
        onChangeText={setPassword}
        secureTextEntry
        autoComplete="new-password"
      />
      <Field
        label="Repeat the new password"
        value={again}
        onChangeText={setAgain}
        secureTextEntry
        autoComplete="new-password"
        onSubmitEditing={submit}
      />
      {mismatch ? <Notice tone="error">The two passwords do not match.</Notice> : null}
      {error ? <Notice tone="error">{error}</Notice> : null}
      <Button
        label="Save new password"
        onPress={submit}
        loading={busy}
        disabled={!password || password !== again}
      />
      <Button label="Back to sign in" kind="secondary" onPress={() => router.replace("/sign-in")} />
    </Page>
  );
}
