import type { QueryClient } from "@tanstack/react-query";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DeviceDetectedPrompt, isMineToConnect } from "@/components/dashboard/device-prompt";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/hooks";
import type { Device } from "@/lib/api/types";
import { demoUser } from "@/lib/demo/data";
import { renderWithClient, signInDemo } from "@/test/utils";

const locket = (over: Partial<Device> = {}): Device => ({
  id: "dev_a",
  deviceId: "ASK-AAAAAA",
  name: "Front desk",
  status: "online",
  simulated: false,
  connected: false,
  lastHeartbeatAt: new Date().toISOString(),
  lastUploadAt: null,
  firmwareVersion: "0.9.2",
  batteryPercent: 80,
  uploadQueueDepth: 0,
  assignedTo: { id: demoUser.id, name: demoUser.name },
  ...over,
});

const prompt = () => screen.queryByRole("region", { name: /locket detected/i });

/**
 * Waits until the prompt has everything it needs (who you are, and the list of lockets) and has had
 * a chance to render, so "nothing is shown" means it decided against it, not that it is still loading.
 */
async function settled(client: QueryClient) {
  await waitFor(() => {
    expect(client.getQueryData(queryKeys.me)).toBeDefined();
    expect(client.getQueryData(queryKeys.devices)).toBeDefined();
  });
  await act(async () => {});
}

function show(devices: Device[], role: "admin" | "manager" | "employee" = "admin") {
  vi.spyOn(api.devices, "list").mockResolvedValue(devices);
  vi.spyOn(api.auth, "me").mockResolvedValue({ ...demoUser, role });
  return renderWithClient(<DeviceDetectedPrompt />);
}

describe("DeviceDetectedPrompt", () => {
  beforeEach(() => {
    signInDemo();
    vi.restoreAllMocks();
  });

  it("offers to connect a locket of yours that is online", async () => {
    show([locket()]);
    const card = await screen.findByRole("region", { name: /locket detected/i });
    expect(within(card).getByRole("heading", { name: "Front desk is online" })).toBeInTheDocument();
    expect(within(card).getByText("ASK-AAAAAA")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Connect" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Not now" })).toBeInTheDocument();
  });

  it("says plainly when the locket is a simulated stand-in", async () => {
    show([locket({ simulated: true })]);
    expect(await screen.findByText("Simulated")).toBeInTheDocument();
  });

  it("stays quiet for lockets that are offline, still pairing, revoked or already connected", async () => {
    const view = show([
      locket({ id: "a", status: "offline" }),
      locket({ id: "b", status: "pending" }),
      locket({ id: "c", status: "revoked" }),
      locket({ id: "d", connected: true }),
    ]);
    await settled(view.client);
    expect(prompt()).not.toBeInTheDocument();
  });

  it("does not interrupt for a locket that belongs to someone else, even a manager", async () => {
    const view = show([locket({ assignedTo: { id: "usr_other", name: "Priya Nair" } })], "admin");
    await settled(view.client);
    expect(prompt()).not.toBeInTheDocument();
  });

  it("offers a manager a locket that nobody has been assigned yet, but not an employee", async () => {
    const unassigned = locket({ assignedTo: null });
    const manager = show([unassigned], "manager");
    expect(await screen.findByRole("region", { name: /locket detected/i })).toBeInTheDocument();
    manager.unmount();
    window.sessionStorage.clear();

    const employee = show([unassigned], "employee");
    await settled(employee.client);
    expect(prompt()).not.toBeInTheDocument();
  });

  it("connects, confirms, and goes away", async () => {
    const connect = vi.spyOn(api.devices, "connect").mockResolvedValue(locket({ connected: true }));
    show([locket()]);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Connect" }));

    await waitFor(() => expect(connect).toHaveBeenCalledWith("dev_a"));
    await waitFor(() => expect(prompt()).not.toBeInTheDocument());
  });

  it("keeps the offer open and explains when the server says no", async () => {
    vi.spyOn(api.devices, "connect").mockRejectedValue(
      new ApiError(409, "device_offline", "This locket is not online right now."),
    );
    show([locket()]);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Connect" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("This locket is not online right now.");
    expect(prompt()).toBeInTheDocument();
  });

  it("goes away on 'Not now' and does not come back for that locket", async () => {
    const view = show([locket()]);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Not now" }));
    await waitFor(() => expect(prompt()).not.toBeInTheDocument());

    // The list refreshes (as it does after every live event) and the locket is still unconnected.
    await view.client.invalidateQueries({ queryKey: queryKeys.devices });
    await settled(view.client);
    expect(api.devices.list).toHaveBeenCalledTimes(2);
    expect(prompt()).not.toBeInTheDocument();
  });

  it("offers each locket once per browser session, even across a reload", async () => {
    const first = show([locket()]);
    await screen.findByRole("region", { name: /locket detected/i });
    first.unmount(); // a reload: new page, same browser session

    const reloaded = renderWithClient(<DeviceDetectedPrompt />);
    await settled(reloaded.client);
    expect(prompt()).not.toBeInTheDocument();
  });

  it("offers a different locket in the same session", async () => {
    const first = show([locket()]);
    await screen.findByRole("region", { name: /locket detected/i });
    first.unmount();

    show([locket(), locket({ id: "dev_b", deviceId: "ASK-BBBBBB", name: "Warehouse" })]);
    expect(await screen.findByRole("heading", { name: "Warehouse is online" })).toBeInTheDocument();
  });

  it("shows one locket at a time and moves to the next after the first is dealt with", async () => {
    show([locket(), locket({ id: "dev_b", deviceId: "ASK-BBBBBB", name: "Warehouse" })]);
    const user = userEvent.setup();

    expect(await screen.findByRole("heading", { name: "Front desk is online" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Warehouse is online" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Not now" }));
    expect(await screen.findByRole("heading", { name: "Warehouse is online" })).toBeInTheDocument();
  });

  it("closes on Escape, unless a dialog is open and needs the key", async () => {
    show([locket()]);
    const user = userEvent.setup();
    await screen.findByRole("region", { name: /locket detected/i });

    const dialog = document.body.appendChild(document.createElement("div"));
    dialog.setAttribute("role", "dialog");
    await user.keyboard("{Escape}");
    expect(prompt()).toBeInTheDocument();

    dialog.remove();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(prompt()).not.toBeInTheDocument());
  });

  it("still works when the browser's storage is blocked or holds rubbish", async () => {
    window.sessionStorage.setItem("ask.device-prompts", "{{{ not json");
    show([locket()]);
    expect(await screen.findByRole("region", { name: /locket detected/i })).toBeInTheDocument();

    const blocked = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    renderWithClient(<DeviceDetectedPrompt />);
    expect(await screen.findAllByRole("region", { name: /locket detected/i })).not.toHaveLength(0);
    blocked.mockRestore();
  });
});

describe("isMineToConnect", () => {
  const admin = { ...demoUser, role: "admin" as const };
  const employee = { ...demoUser, role: "employee" as const };

  it("is true only for an online, unconnected locket that is yours or free for a manager to take", () => {
    expect(isMineToConnect(locket(), employee)).toBe(true);
    expect(isMineToConnect(locket({ assignedTo: null }), admin)).toBe(true);
    expect(isMineToConnect(locket({ assignedTo: null }), employee)).toBe(false);
    expect(isMineToConnect(locket({ assignedTo: { id: "other", name: "O" } }), admin)).toBe(false);
    expect(isMineToConnect(locket({ connected: true }), employee)).toBe(false);
    expect(isMineToConnect(locket({ status: "offline" }), employee)).toBe(false);
  });
});
