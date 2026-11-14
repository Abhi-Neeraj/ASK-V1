import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TeamSection } from "@/components/dashboard/team-section";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import type { Invitation, Member } from "@/lib/api/types";
import { demoUser } from "@/lib/demo/data";
import { renderWithClient, signInDemo } from "@/test/utils";

const member = (overrides: Partial<Member> & Pick<Member, "id" | "name">): Member => ({
  email: `${overrides.id}@example.com`,
  role: "employee",
  isActive: true,
  ...overrides,
});

const people: Member[] = [
  member({ id: demoUser.id, name: "Demo Administrator", role: "admin" }),
  member({ id: "usr_1", name: "Priya Nair" }),
  member({ id: "usr_2", name: "Marcus Webb", role: "manager" }),
  member({ id: "usr_3", name: "Sam Okafor", isActive: false }),
];

const rowOf = (name: string) => screen.getByText(name).closest("li")!;

describe("managing team members", () => {
  beforeEach(() => {
    signInDemo();
    vi.restoreAllMocks();
    vi.spyOn(api.team, "members").mockResolvedValue(structuredClone(people));
  });

  describe("who is offered the controls", () => {
    it("offers an administrator controls for everyone but themselves", async () => {
      renderWithClient(<TeamSection viewerRole="admin" viewerId={demoUser.id} />);
      await screen.findByText("Priya Nair");

      expect(within(rowOf("Priya Nair")).getByRole("combobox", { name: "Role for Priya Nair" })).toBeEnabled();
      expect(within(rowOf("Marcus Webb")).getByRole("button", { name: /switch off/i })).toBeInTheDocument();
      // Nobody changes themselves: there is a role badge, and no way to switch themselves off.
      const self = rowOf("Demo Administrator");
      expect(within(self).queryByRole("combobox")).not.toBeInTheDocument();
      expect(within(self).queryByRole("button")).not.toBeInTheDocument();
      expect(within(self).getByText("Administrator")).toBeInTheDocument();
    });

    it.each(["manager", "employee"] as const)("shows a %s names and roles only, with no controls", async (role) => {
      renderWithClient(<TeamSection viewerRole={role} viewerId="usr_2" />);
      await screen.findByText("Priya Nair");
      expect(screen.queryByRole("combobox", { name: /role for/i })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /switch off|sign out everywhere/i })).not.toBeInTheDocument();
    });

    it("shows no email line for someone whose address the server did not send", async () => {
      vi.spyOn(api.team, "members").mockResolvedValue([member({ id: "usr_1", name: "Priya Nair", email: null })]);
      renderWithClient(<TeamSection viewerRole="employee" viewerId="usr_9" />);
      await screen.findByText("Priya Nair");
      expect(rowOf("Priya Nair").textContent).not.toMatch(/@|null/);
    });
  });

  describe("changing a role", () => {
    it("asks the server for the chosen role", async () => {
      const update = vi.spyOn(api.team, "updateMember").mockResolvedValue({ ...people[1]!, role: "manager" });
      const user = userEvent.setup();
      renderWithClient(<TeamSection viewerRole="admin" viewerId={demoUser.id} />);
      await screen.findByText("Priya Nair");

      await user.selectOptions(screen.getByRole("combobox", { name: "Role for Priya Nair" }), "manager");
      await waitFor(() => expect(update).toHaveBeenCalledWith("usr_1", { role: "manager" }));
    });

    it("cannot change the role of someone who is switched off", async () => {
      renderWithClient(<TeamSection viewerRole="admin" viewerId={demoUser.id} />);
      await screen.findByText("Sam Okafor");
      expect(screen.getByRole("combobox", { name: "Role for Sam Okafor" })).toBeDisabled();
    });
  });

  describe("switching someone off and on", () => {
    it("explains what happens, and does nothing until confirmed", async () => {
      const update = vi.spyOn(api.team, "updateMember").mockResolvedValue({ ...people[1]!, isActive: false });
      const user = userEvent.setup();
      renderWithClient(<TeamSection viewerRole="admin" viewerId={demoUser.id} />);
      await screen.findByText("Priya Nair");

      await user.click(within(rowOf("Priya Nair")).getByRole("button", { name: /switch off/i }));
      const dialog = await screen.findByRole("dialog", { name: /switch off priya nair/i });
      expect(dialog).toHaveTextContent(/signed out everywhere and cannot sign in/i);
      expect(dialog).toHaveTextContent(/lockets assigned to them stay assigned/i);
      expect(update).not.toHaveBeenCalled();

      await user.click(within(dialog).getByRole("button", { name: /^switch off$/i }));
      await waitFor(() => expect(update).toHaveBeenCalledWith("usr_1", { isActive: false }));
    });

    it("marks who is switched off and offers to switch them back on", async () => {
      const update = vi.spyOn(api.team, "updateMember").mockResolvedValue({ ...people[3]!, isActive: true });
      const user = userEvent.setup();
      renderWithClient(<TeamSection viewerRole="admin" viewerId={demoUser.id} />);
      await screen.findByText("Sam Okafor");

      expect(within(rowOf("Sam Okafor")).getByText("Switched off")).toBeInTheDocument();
      expect(within(rowOf("Priya Nair")).queryByText("Switched off")).not.toBeInTheDocument();
      expect(within(rowOf("Sam Okafor")).queryByRole("button", { name: /^switch off$/i })).not.toBeInTheDocument();

      await user.click(within(rowOf("Sam Okafor")).getByRole("button", { name: /switch back on/i }));
      const dialog = await screen.findByRole("dialog", { name: /switch sam okafor back on/i });
      await user.click(within(dialog).getByRole("button", { name: /^switch back on$/i }));
      await waitFor(() => expect(update).toHaveBeenCalledWith("usr_3", { isActive: true }));
    });

    it("keeps the dialog open and says why when the server refuses", async () => {
      vi.spyOn(api.team, "updateMember").mockRejectedValue(
        new ApiError(409, "shared_account", "This account also belongs to another company."),
      );
      const user = userEvent.setup();
      renderWithClient(<TeamSection viewerRole="admin" viewerId={demoUser.id} />);
      await screen.findByText("Priya Nair");

      await user.click(within(rowOf("Priya Nair")).getByRole("button", { name: /switch off/i }));
      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: /^switch off$/i }));
      await waitFor(() => expect(api.team.updateMember).toHaveBeenCalled());
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("signs someone out everywhere without switching them off", async () => {
      const endSessions = vi.spyOn(api.team, "endMemberSessions").mockResolvedValue(undefined);
      const update = vi.spyOn(api.team, "updateMember");
      const user = userEvent.setup();
      renderWithClient(<TeamSection viewerRole="admin" viewerId={demoUser.id} />);
      await screen.findByText("Marcus Webb");

      await user.click(within(rowOf("Marcus Webb")).getByRole("button", { name: /sign out everywhere/i }));
      const dialog = await screen.findByRole("dialog", { name: /sign marcus webb out everywhere/i });
      expect(dialog).toHaveTextContent(/can sign in again/i);
      await user.click(within(dialog).getByRole("button", { name: /^sign out everywhere$/i }));
      await waitFor(() => expect(endSessions).toHaveBeenCalledWith("usr_2"));
      expect(update).not.toHaveBeenCalled();
    });
  });

  describe("inviting when the link only goes by email", () => {
    const emailed: Invitation = {
      id: "inv_9",
      email: "new.person@example.com",
      role: "employee",
      createdAt: new Date().toISOString(),
      expiresAt: new Date(Date.now() + 72 * 3600_000).toISOString(),
      accepted: false,
      acceptUrl: null,
    };

    it("says it was emailed, shows no link, and offers no copy button", async () => {
      vi.spyOn(api.team, "invite").mockResolvedValue(emailed);
      const user = userEvent.setup();
      renderWithClient(<TeamSection viewerRole="admin" viewerId={demoUser.id} />);
      await user.click(await screen.findByRole("button", { name: /invite a teammate/i }));
      const dialog = await screen.findByRole("dialog");
      await user.type(within(dialog).getByLabelText(/work email/i), "new.person@example.com");
      await user.click(within(dialog).getByRole("button", { name: /create invitation/i }));

      expect(await screen.findByText("The invitation has been emailed.")).toBeInTheDocument();
      expect(dialog).toHaveTextContent(/only the person it was sent to can use it/i);
      expect(within(dialog).queryByRole("button", { name: /copy link/i })).not.toBeInTheDocument();
      expect(dialog.textContent).not.toMatch(/register\?token/);
    });

    it("still shows and copies the link when the server hands one back (development)", async () => {
      vi.spyOn(api.team, "invite").mockResolvedValue({ ...emailed, acceptUrl: "http://localhost:3000/register?token=abc" });
      const user = userEvent.setup();
      renderWithClient(<TeamSection viewerRole="admin" viewerId={demoUser.id} />);
      await user.click(await screen.findByRole("button", { name: /invite a teammate/i }));
      const dialog = await screen.findByRole("dialog");
      await user.type(within(dialog).getByLabelText(/work email/i), "new.person@example.com");
      await user.click(within(dialog).getByRole("button", { name: /create invitation/i }));

      expect(await within(dialog).findByText(/register\?token=abc/)).toBeInTheDocument();
      expect(within(dialog).getByRole("button", { name: /copy link/i })).toBeInTheDocument();
    });
  });
});
