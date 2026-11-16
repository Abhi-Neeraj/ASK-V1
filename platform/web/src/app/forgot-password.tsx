import React from "react";
import { router } from "expo-router";
import { useSession } from "@/application/session";
import { Button, Field, Notice, Page } from "@/components/ui";

export default function ForgotPassword() {
  const { api } = useSession();
  const [email, setEmail] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [sent, setSent] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.requestPasswordReset(email.trim());
      setSent(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That did not work. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Page title="Reset password" subtitle="We email you a link to choose a new password." back>
      <Field label="Email" value={email} onChangeText={setEmail} keyboardType="email-address" onSubmitEditing={submit} />
      {sent ? <Notice tone="success">If that address has an account, a reset link is on its way.</Notice> : null}
      {error ? <Notice tone="error">{error}</Notice> : null}
      <Button label="Send reset link" onPress={submit} loading={busy} disabled={!email} />
      <Button label="Back to sign in" kind="secondary" onPress={() => router.replace("/sign-in")} />
    </Page>
  );
}
