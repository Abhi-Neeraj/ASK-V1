import React from "react";
import { useSession } from "@/application/session";
import { Button, Field, Notice, Page } from "@/components/ui";

export default function Account() {
  const { api } = useSession();
  const [current, setCurrent] = React.useState("");
  const [next, setNext] = React.useState("");
  const [again, setAgain] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [done, setDone] = React.useState(false);

  const submit = async () => {
    if (next !== again) {
      setError("The two new passwords are different.");
      return;
    }
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      await api.changePassword(current, next);
      setDone(true);
      setCurrent("");
      setNext("");
      setAgain("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "The password did not change.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Page title="Change password" subtitle="At least 12 characters." back>
      <Field label="Current password" value={current} onChangeText={setCurrent} secureTextEntry autoComplete="current-password" />
      <Field label="New password" value={next} onChangeText={setNext} secureTextEntry autoComplete="new-password" />
      <Field label="New password again" value={again} onChangeText={setAgain} secureTextEntry autoComplete="new-password" onSubmitEditing={submit} />
      {error ? <Notice tone="error">{error}</Notice> : null}
      {done ? <Notice tone="success">Your password is changed. Other devices are signed out.</Notice> : null}
      <Button label="Change password" onPress={submit} loading={busy} disabled={!current || !next || !again} />
    </Page>
  );
}
