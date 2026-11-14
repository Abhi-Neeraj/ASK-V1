import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DeviceCard } from "@/components/dashboard/device-card";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/api/hooks";
import type { Device } from "@/lib/api/types";
import { demoUser } from "@/lib/demo/data";
import { renderWithClient, signInDemo } from "@/test/utils";

const base: Device = {
  id: "dev_1",
  deviceId: "ASK-A7F2",
  name: "Locket A7F2",
  status: "online",
  simulated: false,
  connected: false,
  lastHeartbeatAt: new Date().toISOString(),
  lastUploadAt: null,
  firmwareVersion: "0.9.2",
  batteryPercent: 78,
  uploadQueueDepth: 0,
  assignedTo: { id: "usr_1", name: "Priya Nair" },
};

describe("DeviceCard", () => {
  beforeEach(() => {
    signInDemo();
    vi.restoreAllMocks();
  });

  it("shows only what the device really reports", () => {
    renderWithClient(<DeviceCard device={{ ...base, batteryPercent: null, firmwareVersion: null, uploadQueueDepth: null }} />);
    expect(screen.getAllByText("Not reported")).toHaveLength(3);
    expect(screen.getByText("Never")).toBeInTheDocument();
  });

  it("labels a simulated locket so it is never mistaken for real hardware", () => {
    renderWithClient(<DeviceCard device={{ ...base, simulated: true }} />);
    expect(screen.getByText("Simulated")).toHaveAttribute("title", expect.stringMatching(/not real hardware/i));
  });

  it("shows no such label on a real locket", () => {
    renderWithClient(<DeviceCard device={base} />);
    expect(screen.queryByText("Simulated")).not.toBeInTheDocument();
  });

  it("shows reported battery and queue values", () => {
    renderWithClient(<DeviceCard device={{ ...base, uploadQueueDepth: 3 }} />);
    expect(screen.getByText("78%")).toBeInTheDocument();
    expect(screen.getByText("3 waiting")).toBeInTheDocument();
  });

  it("connects an online locket", async () => {
    const connect = vi.spyOn(api.devices, "connect").mockResolvedValue({ ...base, connected: true });
    renderWithClient(<DeviceCard device={base} />);
    await userEvent.setup().click(screen.getByRole("button", { name: /^connect$/i }));
    await waitFor(() => expect(connect).toHaveBeenCalledWith("dev_1"));
  });

  it("does not allow connecting an offline locket and says why", () => {
    renderWithClient(<DeviceCard device={{ ...base, status: "offline" }} />);
    expect(screen.getByRole("button", { name: /^connect$/i })).toBeDisabled();
    expect(screen.getByText(/connect becomes available when the locket is online/i)).toBeInTheDocument();
  });

  it("explains that a pending locket must finish pairing", () => {
    renderWithClient(<DeviceCard device={{ ...base, status: "pending" }} />);
    expect(screen.getByRole("button", { name: /^connect$/i })).toBeDisabled();
    expect(screen.getByText(/finish pairing on the locket first/i)).toBeInTheDocument();
  });

  it("disconnects without deleting anything", async () => {
    const disconnect = vi.spyOn(api.devices, "disconnect").mockResolvedValue({ ...base, connected: false });
    renderWithClient(<DeviceCard device={{ ...base, connected: true }} />);
    expect(screen.queryByRole("button", { name: /^connect$/i })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: /disconnect/i }));
    await waitFor(() => expect(disconnect).toHaveBeenCalledWith("dev_1"));
  });

  describe("managing a locket", () => {
    it("lets a manager assign the locket to a teammate", async () => {
      const assign = vi
        .spyOn(api.devices, "assign")
        .mockResolvedValue({ ...base, assignedTo: { id: "usr_2", name: "Marcus Webb" } });
      renderWithClient(<DeviceCard device={base} />);

      const select = await screen.findByRole("combobox", { name: /who wears locket a7f2/i });
      await waitFor(() => expect(select).toBeEnabled()); // enabled once the team has loaded
      await userEvent.setup().selectOptions(select, "usr_2");

      await waitFor(() => expect(assign).toHaveBeenCalledWith("dev_1", "usr_2"));
    });

    it("says what stays with whom when a locket changes hands", async () => {
      renderWithClient(<DeviceCard device={base} />);
      await screen.findByRole("combobox", { name: /who wears/i });
      expect(screen.getByText(/already recorded stays with the person who had it/i)).toBeInTheDocument();
      // Nothing is waiting, so there is nothing to warn about.
      expect(screen.queryByText(/still waiting to upload/i)).not.toBeInTheDocument();
    });

    it("warns that clips still waiting to upload will go to the next wearer", async () => {
      renderWithClient(<DeviceCard device={{ ...base, uploadQueueDepth: 3 }} />);
      await screen.findByRole("combobox", { name: /who wears/i });
      const warning = screen.getByText(/3 recordings are still waiting to upload from this locket/i);
      expect(warning).toHaveTextContent(/wait until the upload queue is empty/i);
    });

    it("uses the singular for a single waiting recording", async () => {
      renderWithClient(<DeviceCard device={{ ...base, uploadQueueDepth: 1 }} />);
      await screen.findByRole("combobox", { name: /who wears/i });
      expect(screen.getByText(/1 recording is still waiting to upload/i)).toBeInTheDocument();
    });

    it("shows an employee none of this, since they cannot hand the locket on", async () => {
      const me = vi.spyOn(api.auth, "me").mockResolvedValue({
        id: "usr_1",
        email: "p@example.com",
        name: "Priya Nair",
        role: "employee",
        emailVerified: true,
        company: { id: "c1", name: "Northwind Demo Co." },
      });
      renderWithClient(<DeviceCard device={{ ...base, uploadQueueDepth: 3 }} />);
      // Absence only means something once the person's role has arrived.
      await waitFor(() => expect(me).toHaveBeenCalled());
      await me.mock.results[0]!.value;
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(screen.queryByRole("combobox", { name: /who wears/i })).not.toBeInTheDocument();
      expect(screen.queryByText(/already recorded stays/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/still waiting to upload/i)).not.toBeInTheDocument();
    });

    it("can clear the assignment", async () => {
      const assign = vi.spyOn(api.devices, "assign").mockResolvedValue({ ...base, assignedTo: null });
      renderWithClient(<DeviceCard device={base} />);

      const select = await screen.findByRole("combobox", { name: /who wears/i });
      await waitFor(() => expect(select).toBeEnabled());
      await userEvent.setup().selectOptions(select, "");

      await waitFor(() => expect(assign).toHaveBeenCalledWith("dev_1", null));
    });

    it("asks before revoking, and says what revoking does", async () => {
      const revoke = vi.spyOn(api.devices, "revoke").mockResolvedValue({ ...base, status: "revoked" });
      const user = userEvent.setup();
      renderWithClient(<DeviceCard device={base} />);

      await user.click(await screen.findByRole("button", { name: /^revoke$/i }));
      expect(revoke).not.toHaveBeenCalled();
      const dialog = await screen.findByRole("dialog", { name: /revoke locket a7f2/i });
      expect(within(dialog).getByText(/credential stops working immediately/i)).toBeInTheDocument();
      await user.click(within(dialog).getByRole("button", { name: /^revoke locket$/i }));

      await waitFor(() => expect(revoke).toHaveBeenCalledWith("dev_1"));
    });

    it("shows a revoked locket as read-only and explains why", async () => {
      renderWithClient(<DeviceCard device={{ ...base, status: "revoked" }} />);
      expect(screen.getByText(/this locket was revoked and can no longer send recordings/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /^connect$/i })).toBeDisabled();
      // Give the signed-in user's role time to load; the controls must still be absent.
      await waitFor(() => expect(screen.queryByRole("button", { name: /^revoke$/i })).not.toBeInTheDocument());
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    });

    it("gives an employee no management controls", async () => {
      vi.spyOn(api.auth, "me").mockResolvedValue({ ...demoUser, role: "employee" });
      const { client } = renderWithClient(<DeviceCard device={base} />);

      await waitFor(() => expect(client.getQueryData(queryKeys.me)).toMatchObject({ role: "employee" }));
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^revoke$/i })).not.toBeInTheDocument();
      expect(screen.getByText("Priya Nair")).toBeInTheDocument();
    });
  });
});
