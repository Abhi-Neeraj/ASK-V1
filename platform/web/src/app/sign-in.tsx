import React from "react";
import { router } from "expo-router";
import { useSession } from "@/application/session";
import { Button, Field, Notice, Page } from "@/components/ui";

export default function SignIn() {
  const { signIn } = useSession();
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await signIn(email.trim(), password);
      router.replace("/home");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign in did not work. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Page title="Sign in" subtitle="Use the account your company invited you to.">
      <Field label="Email" value={email} onChangeText={setEmail} keyboardType="email-address" autoComplete="email" />
      <Field
        label="Password"
        value={password}
        onChangeText={setPassword}
        secureTextEntry
        autoComplete="current-password"
        onSubmitEditing={submit}
      />
      {error ? <Notice tone="error">{error}</Notice> : null}
      <Button label="Sign in" onPress={submit} loading={busy} disabled={!email || !password} />
      <Button label="Forgot password" kind="secondary" onPress={() => router.push("/forgot-password")} />
    </Page>
  );
}
