import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardShell } from "@/components/dashboard/shell";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import { renderWithClient, signInDemo } from "@/test/utils";

const nav = vi.hoisted(() => ({ replace: vi.fn(), pathname: "/dashboard" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace }),
  usePathname: () => nav.pathname,
}));

describe("DashboardShell", () => {
  beforeEach(() => {
    nav.replace.mockReset();
    nav.pathname = "/dashboard";
    vi.restoreAllMocks();
  });

  it("sends a signed-out visitor to sign in and remembers where they were going", async () => {
    nav.pathname = "/dashboard/devices";
    renderWithClient(
      <DashboardShell>
        <p>private content</p>
      </DashboardShell>,
    );
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/login?next=%2Fdashboard%2Fdevices"));
    expect(screen.queryByText("private content")).not.toBeInTheDocument();
  });

  it("shows the workspace, the user and the demo banner when signed in", async () => {
    signInDemo();
    renderWithClient(
      <DashboardShell>
        <p>private content</p>
      </DashboardShell>,
    );
    expect(await screen.findByText("private content")).toBeInTheDocument();
    expect(screen.getByText("Demo Administrator")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/demo mode/i);
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("marks the current section in the navigation", async () => {
    signInDemo();
    nav.pathname = "/dashboard/recordings/rec_001";
    renderWithClient(
      <DashboardShell>
        <p>x</p>
      </DashboardShell>,
    );
    await screen.findByText("x");
    const current = screen.getAllByRole("link", { name: "Recordings" }).filter((el) => el.getAttribute("aria-current") === "page");
    expect(current.length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: "Overview" }).every((el) => el.getAttribute("aria-current") !== "page")).toBe(true);
  });

  describe("who sees the audit log in the navigation", () => {
    const asRole = (role: "admin" | "manager" | "employee") =>
      vi.spyOn(api.auth, "me").mockResolvedValue({
        id: "u1",
        email: "u@example.com",
        name: "Someone",
        role,
        emailVerified: true,
        company: { id: "c1", name: "Northwind Demo Co." },
      });

    it("shows it to an administrator, in the sidebar and in the phone strip", async () => {
      signInDemo();
      asRole("admin");
      renderWithClient(
        <DashboardShell>
          <p>x</p>
        </DashboardShell>,
      );
      await screen.findByText("x");
      expect(screen.getAllByRole("link", { name: "Audit log" })).toHaveLength(2);
      for (const link of screen.getAllByRole("link", { name: "Audit log" })) {
        expect(link).toHaveAttribute("href", "/dashboard/audit");
      }
    });

    it.each(["manager", "employee"] as const)("hides it from a %s", async (role) => {
      signInDemo();
      const me = asRole(role);
      renderWithClient(
        <DashboardShell>
          <p>x</p>
        </DashboardShell>,
      );
      await screen.findByText("x");
      expect(me).toHaveBeenCalled();
      expect(screen.queryByRole("link", { name: "Audit log" })).not.toBeInTheDocument();
      // The rest of the navigation is unchanged.
      expect(screen.getAllByRole("link", { name: "Settings" })).toHaveLength(2);
    });

    it("labels the page in the breadcrumb trail", async () => {
      signInDemo();
      nav.pathname = "/dashboard/audit";
      renderWithClient(
        <DashboardShell>
          <p>x</p>
        </DashboardShell>,
      );
      await screen.findByText("x");
      expect(screen.getAllByText("Audit log").length).toBeGreaterThan(0);
      expect(screen.queryByText("Recording")).not.toBeInTheDocument();
    });
  });

  it("shows an error with a retry when the server cannot be reached (and does not redirect)", async () => {
    const me = vi.spyOn(api.auth, "me").mockRejectedValue(new ApiError(0, "network_error", "Can't reach the ASK server."));
    renderWithClient(
      <DashboardShell>
        <p>private content</p>
      </DashboardShell>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not load your workspace");
    expect(nav.replace).not.toHaveBeenCalled();
    await userEvent.setup().click(screen.getByRole("button", { name: /try again/i }));
    await waitFor(() => expect(me).toHaveBeenCalledTimes(2));
  });
});
