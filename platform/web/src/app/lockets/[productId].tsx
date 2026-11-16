import React from "react";
import { Text, View } from "react-native";
import { router, useLocalSearchParams } from "expo-router";
import { firmwareOffer, startFirmwareUpdate } from "@/application/firmwareUpdate";
import { formatDate } from "@/application/labels";
import { useLocketController } from "@/application/locketController";
import { canManage, useSession } from "@/application/session";
import { ScreenSection } from "@/components/primitives";
import { Button, Card, Field, Loading, Notice, Page, uiText } from "@/components/ui";
import type { FirmwareLatest, LocketOut } from "@/infrastructure/api/types";

export default function LocketDetail() {
  const { productId } = useLocalSearchParams<{ productId: string }>();
  const { api, user } = useSession();
  const controller = useLocketController();
  const [locket, setLocket] = React.useState<LocketOut | null>(null);
  const [offer, setOffer] = React.useState<FirmwareLatest | null>(null);
  const [limit, setLimit] = React.useState(1);
  const [ssid, setSsid] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [note, setNote] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [confirming, setConfirming] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    try {
      const all = await api.listLockets();
      const mine = all.find((l) => l.productId === productId) ?? null;
      setLocket(mine);
      if (mine) {
        setLimit(mine.maxUsers);
        const version = controller.connected && controller.productId === productId ? controller.firmwareVersion : mine.firmwareVersion;
        setOffer(await firmwareOffer(api, { firmwareVersion: version }));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load this locket.");
    }
  }, [api, productId, controller.connected, controller.productId, controller.firmwareVersion]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const act = async (name: string, work: () => Promise<void>) => {
    setBusy(name);
    setError(null);
    setNote(null);
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That did not work.");
    } finally {
      setBusy(null);
    }
  };

  if (!locket) {
    return (
      <Page title="Locket" back>
        {error ? <Notice tone="error">{error}</Notice> : <Loading />}
      </Page>
    );
  }

  const connected = controller.connected && controller.productId === locket.productId;
  const admin = user?.role === "admin";
  const manager = canManage(user);

  return (
    <Page title={locket.name} subtitle={locket.productId} back>
      {error ? <Notice tone="error">{error}</Notice> : null}
      {note ? <Notice tone="success">{note}</Notice> : null}

      <Card>
        <Text style={uiText.secondary}>Product ID (permanent)</Text>
        <Text style={uiText.mono}>{locket.productId}</Text>
        <Text style={uiText.muted}>
          Firmware {(connected ? controller.firmwareVersion : locket.firmwareVersion) ?? "not reported yet"}
          {locket.batteryPercent !== null ? ` · battery ${locket.batteryPercent}%` : ""}
          {locket.lastSeenAt ? ` · seen ${formatDate(locket.lastSeenAt)}` : ""}
        </Text>
      </Card>

      {controller.available ? (
        connected ? (
          <Button label="Disconnect" kind="secondary" onPress={controller.disconnect} loading={controller.busy} />
        ) : (
          <Button label="Connect" kind="secondary" onPress={() => controller.connectTo(locket.productId)} loading={controller.busy} />
        )
      ) : null}

      <ScreenSection>People on this locket</ScreenSection>
      <Text style={uiText.secondary}>
        {locket.members.length} of {locket.maxUsers} {locket.maxUsers === 1 ? "place" : "places"} used.
      </Text>
      {locket.members.map((m) => {
        const self = m.id === user?.id;
        return (
          <Card key={m.id}>
            <Text style={uiText.strong}>{m.name}</Text>
            <Text style={uiText.secondary}>{m.email}</Text>
            {self || manager ? (
              <Button
                label={
                  confirming === m.id
                    ? "Tap again to confirm"
                    : self
                      ? "Forget this locket"
                      : `Remove ${m.name}`
                }
                kind="danger"
                loading={busy === m.id}
                onPress={() => {
                  if (confirming !== m.id) {
                    setConfirming(m.id);
                    return;
                  }
                  setConfirming(null);
                  void act(m.id, async () => {
                    await api.removeMember(locket.productId, m.id);
                    if (self) {
                      // Tell the locket to drop this phone's pairing too (it is in range only when connected).
                      if (connected) await controller.forgetConnected();
                      router.replace("/lockets");
                    } else {
                      await load();
                    }
                  });
                }}
              />
            ) : null}
          </Card>
        );
      })}

      {admin ? (
        <>
          <ScreenSection>Limit</ScreenSection>
          <Text style={uiText.secondary}>Only administrators can change how many people may use this locket.</Text>
          <View style={{ flexDirection: "row", gap: 12, alignItems: "center" }}>
            <View style={{ flex: 1 }}>
              <Button label="−" kind="secondary" onPress={() => setLimit((n) => Math.max(1, n - 1))} disabled={limit <= 1} />
            </View>
            <Text style={[uiText.strong, { minWidth: 48, textAlign: "center" }]}>{limit}</Text>
            <View style={{ flex: 1 }}>
              <Button label="+" kind="secondary" onPress={() => setLimit((n) => Math.min(50, n + 1))} disabled={limit >= 50} />
            </View>
          </View>
          {limit !== locket.maxUsers ? (
            <Button
              label={`Save limit: ${limit}`}
              loading={busy === "limit"}
              onPress={() =>
                act("limit", async () => {
                  setLocket(await api.setLimit(locket.productId, limit));
                  setNote(`The limit is now ${limit}.`);
                })
              }
            />
          ) : null}
        </>
      ) : null}

      <ScreenSection>Firmware</ScreenSection>
      {!offer ? <Text style={uiText.secondary}>Up to date, as far as the server knows.</Text> : null}
      {offer && !controller.available ? (
        <Notice>Version {offer.version} is available. Install it from the ASK phone app, next to the locket.</Notice>
      ) : null}
      {offer && controller.available && !connected ? (
        <Notice>Version {offer.version} is available. Connect to the locket to install it.</Notice>
      ) : null}
      {offer && controller.available && connected ? (
        <View>
          <Text style={uiText.secondary}>
            Version {offer.version} is available. The locket downloads it over Wi-Fi, so it needs your Wi-Fi name and password once.
          </Text>
          <Field label="Wi-Fi name" value={ssid} onChangeText={setSsid} />
          <Field label="Wi-Fi password" value={password} onChangeText={setPassword} secureTextEntry />
          <Button
            label="Update firmware"
            loading={busy === "ota"}
            onPress={() =>
              act("ota", async () => {
                await startFirmwareUpdate({
                  link: controller.link!,
                  latest: offer,
                  wifi: ssid ? { ssid, password } : undefined,
                });
                setNote("The locket is updating. It restarts by itself when done; keep it charged.");
              })
            }
          />
          {controller.status?.state === "ota" ? <Text style={uiText.secondary}>Updating: {controller.status.otaPercent}%</Text> : null}
        </View>
      ) : null}
    </Page>
  );
}
