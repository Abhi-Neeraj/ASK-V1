import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TeamSection } from "@/components/dashboard/team-section";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import type { Invitation } from "@/lib/api/types";
import { demoUser } from "@/lib/demo/data";
import { renderWithClient, signInDemo } from "@/test/utils";

const pending: Invitation = {
  id: "inv_7",
  email: "waiting@example.com",
  role: "employee",
  createdAt: new Date().toISOString(),
  expiresAt: new Date(Date.now() + 48 * 3600_000).toISOString(),
  accepted: false,
  acceptUrl: null,
};

describe("TeamSection", () => {
  beforeEach(() => {
    signInDemo();
    vi.restoreAllMocks();
  });

  it("lists the team and marks the signed-in person", async () => {
    renderWithClient(<TeamSection viewerRole="admin" viewerId={demoUser.id} />);
    expect(await screen.findByText("Priya Nair")).toBeInTheDocument();
    expect(screen.getByText("(you)")).toBeInTheDocument();
  });

  it("lets an administrator pick any role", async () => {
    const user = userEvent.setup();
    renderWithClient(<TeamSection viewerRole="admin" viewerId={demoUser.id} />);
    await user.click(await screen.findByRole("button", { name: /invite a teammate/i }));

    const role = within(await screen.findByRole("dialog")).getByRole("combobox", { name: /role/i });
    expect(role).toBeEnabled();
    expect(within(role).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Employee",
      "Manager",
      "Administrator",
    ]);
  });

  it("limits a manager to inviting employees", async () => {
    const user = userEvent.setup();
    renderWithClient(<TeamSection viewerRole="manager" viewerId="usr_2" />);
    await user.click(await screen.findByRole("button", { name: /invite a teammate/i }));

    const role = within(await screen.findByRole("dialog")).getByRole("combobox", { name: /role/i });
    expect(role).toBeDisabled();
    expect(within(role).getAllByRole("option").map((o) => o.textContent)).toEqual(["Employee"]);
  });

  it("rejects a malformed address without calling the server", async () => {
    const invite = vi.spyOn(api.team, "invite");
    const user = userEvent.setup();
    renderWithClient(<TeamSection viewerRole="admin" viewerId={demoUser.id} />);
    await user.click(await screen.findByRole("button", { name: /invite a teammate/i }));

    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/work email/i), "not-an-email");
    await user.click(within(dialog).getByRole("button", { name: /create invitation/i }));

    expect(await within(dialog).findByText(/enter a valid email/i)).toBeInTheDocument();
    expect(invite).not.toHaveBeenCalled();
  });

  it("creates an invitation and shows the one-time link", async () => {
    const invite = vi.spyOn(api.team, "invite").mockResolvedValue({
      ...pending,
      email: "new.person@example.com",
      role: "manager",
      acceptUrl: "http://localhost:3000/register?token=abc123",
    });
    const user = userEvent.setup();
    renderWithClient(<TeamSection viewerRole="admin" viewerId={demoUser.id} />);
    await user.click(await screen.findByRole("button", { name: /invite a teammate/i }));

    const dialog = await screen.findByRole("dialog", { name: /invite a teammate/i });
    await user.type(within(dialog).getByLabelText(/work email/i), "  new.person@example.com ");
    await user.selectOptions(within(dialog).getByRole("combobox", { name: /role/i }), "manager");
    await user.click(within(dialog).getByRole("button", { name: /create invitation/i }));

    await waitFor(() => expect(invite).toHaveBeenCalledWith({ email: "new.person@example.com", role: "manager" }));
    const done = await screen.findByRole("dialog", { name: /invitation created/i });
    expect(within(done).getByText("http://localhost:3000/register?token=abc123")).toBeInTheDocument();
    expect(within(done).getByText(/only time the link is shown/i)).toBeInTheDocument();
  });

  it("shows the server's reason when an invitation is refused", async () => {
    vi.spyOn(api.team, "invite").mockRejectedValue(
      new ApiError(409, "account_exists", "That person already has an account."),
    );
    const user = userEvent.setup();
    renderWithClient(<TeamSection viewerRole="admin" viewerId={demoUser.id} />);
    await user.click(await screen.findByRole("button", { name: /invite a teammate/i }));

    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/work email/i), "priya.nair@example.com");
    await user.click(within(dialog).getByRole("button", { name: /create invitation/i }));

    expect(await within(dialog).findByText("That person already has an account.")).toBeInTheDocument();
  });

  it("revokes a pending invitation after confirmation", async () => {
    vi.spyOn(api.team, "invitations").mockResolvedValue([pending]);
    const revoke = vi.spyOn(api.team, "revokeInvitation").mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderWithClient(<TeamSection viewerRole="admin" viewerId={demoUser.id} />);

    expect(await screen.findByText("waiting@example.com")).toBeInTheDocument();
    expect(screen.getByText("Waiting")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^revoke$/i }));
    expect(revoke).not.toHaveBeenCalled();

    const dialog = await screen.findByRole("dialog", { name: /revoke the invitation for waiting@example.com/i });
    await user.click(within(dialog).getByRole("button", { name: /revoke invitation/i }));
    await waitFor(() => expect(revoke).toHaveBeenCalledWith("inv_7"));
  });
});
