import React from "react";
import { Text, View } from "react-native";
import { router, useFocusEffect } from "expo-router";
import { pairLocket } from "@/application/pairLocket";
import { useLocketController } from "@/application/locketController";
import { useSession } from "@/application/session";
import { ScreenSection } from "@/components/primitives";
import { Button, Card, Field, Loading, Notice, Page, uiText } from "@/components/ui";
import type { FoundLocket } from "@/domain/locket/link";
import type { LocketOut, LocketRegistered } from "@/infrastructure/api/types";

export default function Lockets() {
  const { api, user } = useSession();
  const controller = useLocketController();
  const [lockets, setLockets] = React.useState<LocketOut[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [note, setNote] = React.useState<string | null>(null);
  const [found, setFound] = React.useState<FoundLocket[]>([]);
  const [scanning, setScanning] = React.useState(false);
  const [pairing, setPairing] = React.useState<string | null>(null);
  const [newName, setNewName] = React.useState("");
  const [registered, setRegistered] = React.useState<LocketRegistered | null>(null);
  const [busy, setBusy] = React.useState(false);
  const stopScan = React.useRef<(() => void) | null>(null);

  const load = React.useCallback(async () => {
    try {
      setLockets(await api.listLockets());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load lockets.");
    }
  }, [api]);

  useFocusEffect(
    React.useCallback(() => {
      void load();
      return () => stopScan.current?.();
    }, [load]),
  );

  const scan = () => {
    setFound([]);
    setScanning(true);
    stopScan.current = controller.scan(
      (locket) => setFound((current) => (current.some((f) => f.id === locket.id) ? current : [...current, locket])),
      (message) => {
        setError(message);
        setScanning(false);
      },
    );
    setTimeout(() => {
      stopScan.current?.();
      setScanning(false);
    }, 12000);
  };

  const pair = async (locket: FoundLocket) => {
    if (!controller.link) return;
    stopScan.current?.();
    setScanning(false);
    setPairing(locket.id);
    setError(null);
    setNote(null);
    const outcome = await pairLocket({
      link: controller.link,
      api,
      deviceId: locket.id,
      productId: locket.name,
      onCodeSent: (to) => setNote(`We emailed a 6-digit code to ${to}. Type it when your phone or browser asks for the Bluetooth pairing code.`),
    });
    setPairing(null);
    if (outcome.ok) {
      setNote(`Paired with ${locket.name}.`);
      await controller.attach(locket); // pairing left the link open: adopt it, do not connect twice
      await load();
    } else {
      setError(outcome.message);
    }
  };

  const register = async () => {
    setBusy(true);
    setError(null);
    try {
      setRegistered(await api.registerLocket(newName.trim()));
      setNewName("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not register the locket.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Page title="Lockets" subtitle="Each locket has a permanent product ID. Pairing needs a code we email you." back>
      {error ? <Notice tone="error">{error}</Notice> : null}
      {note ? <Notice tone="success">{note}</Notice> : null}

      <ScreenSection>Your lockets</ScreenSection>
      {lockets === null ? <Loading /> : null}
      {lockets?.length === 0 ? <Text style={uiText.secondary}>You are not on any locket yet.</Text> : null}
      {lockets?.map((l) => (
        <Card key={l.productId} label={`Open ${l.productId}`} onPress={() => router.push(`/lockets/${l.productId}`)}>
          <Text style={uiText.strong}>{l.name}</Text>
          <Text style={uiText.mono}>{l.productId}</Text>
          <Text style={uiText.muted}>
            {l.members.length} of {l.maxUsers} {l.maxUsers === 1 ? "person" : "people"}
            {l.firmwareVersion ? ` · firmware ${l.firmwareVersion}` : ""}
          </Text>
          {controller.available && l.members.some((m) => m.id === user?.id) ? (
            controller.connected && controller.productId === l.productId ? (
              <Text style={uiText.secondary}>Connected</Text>
            ) : (
              <Button label="Connect" kind="secondary" loading={controller.busy} onPress={() => controller.connectTo(l.productId)} />
            )
          ) : null}
        </Card>
      ))}

      <ScreenSection>Pair a locket</ScreenSection>
      {controller.available ? (
        <View>
          <Text style={uiText.secondary}>Turn the locket on and keep it next to your phone or computer.</Text>
          <Button label={scanning ? "Looking for lockets…" : "Find lockets nearby"} onPress={scan} disabled={scanning} />
          {found.map((f) => (
            <Card key={f.id}>
              <Text style={uiText.strong}>{f.name}</Text>
              <Button label={`Pair with ${f.name}`} onPress={() => pair(f)} loading={pairing === f.id} />
            </Card>
          ))}
          {controller.message ? <Notice>{controller.message}</Notice> : null}
        </View>
      ) : (
        <Notice>Pair from the ASK phone app. A website cannot reach the locket over Bluetooth.</Notice>
      )}

      {user?.role === "admin" ? (
        <>
          <ScreenSection>Register a new locket</ScreenSection>
          <Text style={uiText.secondary}>For new lockets from the factory. The secret is shown once, to write into the locket.</Text>
          <Field label="Name" value={newName} onChangeText={setNewName} placeholder="Meeting room locket" />
          <Button label="Register" onPress={register} loading={busy} disabled={!newName.trim()} />
          {registered ? (
            <Card>
              <Text style={uiText.strong}>{registered.productId}</Text>
              <Text style={uiText.secondary}>Type this on the locket's USB serial console now. It is not shown again:</Text>
              <Text selectable style={uiText.mono}>
                provision {registered.productId} {registered.secretHex}
              </Text>
            </Card>
          ) : null}
        </>
      ) : null}
    </Page>
  );
}
