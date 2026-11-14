import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuditView } from "@/components/dashboard/views/audit-view";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import type { AuditEvent, Page, User } from "@/lib/api/types";
import { renderWithClient, signInDemo } from "@/test/utils";

const event = (overrides: Partial<AuditEvent> & Pick<AuditEvent, "id" | "action">): AuditEvent => ({
  at: "2026-10-20T12:00:00.000Z",
  category: overrides.action.split(".")[0] ?? "",
  actor: { kind: "user", id: "u1", name: "Demo Administrator", email: "admin@example.com" },
  objectType: null,
  objectId: null,
  detail: null,
  ip: null,
  ...overrides,
});

const page = (items: AuditEvent[], total = items.length): Page<AuditEvent> => ({ items, total, page: 1, pageSize: 20 });

const employee: User = {
  id: "e1",
  email: "e@example.com",
  name: "An Employee",
  role: "employee",
  emailVerified: true,
  company: { id: "c1", name: "Northwind Demo Co." },
};

describe("AuditView", () => {
  beforeEach(() => {
    signInDemo();
    vi.restoreAllMocks();
  });

  it("lists what happened in words, with who did it and the exact action name", async () => {
    vi.spyOn(api.audit, "list").mockResolvedValue(
      page([
        event({
          id: "a",
          action: "settings.updated",
          objectType: "company",
          objectId: "cmp_12345678",
          detail: { retention_days: 90 },
          ip: "203.0.113.7",
        }),
        event({ id: "b", action: "auth.login_failed", actor: null }),
      ]),
    );
    renderWithClient(<AuditView />);
    const list = await screen.findByRole("list", { name: "Recorded actions" });
    const [settings, failed] = within(list).getAllByRole("listitem");
    expect(settings).toHaveTextContent("Changed workspace settings");
    expect(settings).toHaveTextContent("settings.updated");
    expect(settings).toHaveTextContent("Demo Administrator");
    expect(settings).toHaveTextContent("Workspace cmp_1234");
    expect(settings).toHaveTextContent("from 203.0.113.7");
    // A label and its value, as a definition list: "retention days" is the term and "90" its definition.
    expect(within(settings).getByText("retention days").tagName).toBe("DT");
    expect(within(settings).getByText("90").tagName).toBe("DD");
    expect(failed).toHaveTextContent("Failed sign-in attempt");
    expect(failed).toHaveTextContent("System or removed account");
  });

  it("links a recording that still exists and not one that was deleted", async () => {
    vi.spyOn(api.audit, "list").mockResolvedValue(
      page([
        event({ id: "a", action: "recording.downloaded", objectType: "recording", objectId: "rec_aaaa1111" }),
        event({ id: "b", action: "recording.deleted", objectType: "recording", objectId: "rec_bbbb2222" }),
      ]),
    );
    renderWithClient(<AuditView />);
    expect(await screen.findByRole("link", { name: "rec_aaaa" })).toHaveAttribute(
      "href",
      "/dashboard/recordings/rec_aaaa1111",
    );
    expect(screen.queryByRole("link", { name: "rec_bbbb" })).not.toBeInTheDocument();
    expect(screen.getByText("rec_bbbb")).toBeInTheDocument();
  });

  it("asks the server for a kind of action, and for a day range in the right order", async () => {
    const list = vi.spyOn(api.audit, "list").mockResolvedValue(page([event({ id: "a", action: "auth.login" })]));
    const user = userEvent.setup();
    renderWithClient(<AuditView />);
    await screen.findByRole("list", { name: "Recorded actions" });

    await user.selectOptions(screen.getByLabelText("Filter by kind of action"), "device");
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ category: "device", page: 1 })));

    await user.type(screen.getByLabelText("From date"), "2026-10-01");
    await user.type(screen.getByLabelText("To date"), "2026-10-20");
    await waitFor(() => {
      const last = list.mock.calls.at(-1)![0];
      expect(new Date(last.dateFrom!).getTime()).toBeLessThan(new Date(last.dateTo!).getTime());
      expect(last.category).toBe("device");
    });
  });

  it("goes back to the first page when a filter changes", async () => {
    const list = vi.spyOn(api.audit, "list").mockResolvedValue(page([event({ id: "a", action: "auth.login" })], 45));
    const user = userEvent.setup();
    renderWithClient(<AuditView />);
    await screen.findByText("Page 1 of 3");

    await user.click(screen.getByRole("button", { name: /next/i }));
    await screen.findByText("Page 2 of 3");
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }));

    await user.selectOptions(screen.getByLabelText("Filter by kind of action"), "auth");
    await screen.findByText("Page 1 of 3");
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ category: "auth", page: 1 }));
  });

  it("says nothing matched, and clears the filters", async () => {
    vi.spyOn(api.audit, "list").mockImplementation(async (filters) =>
      filters.category ? page([]) : page([event({ id: "a", action: "auth.login" })]),
    );
    const user = userEvent.setup();
    renderWithClient(<AuditView />);
    await screen.findByRole("list", { name: "Recorded actions" });

    await user.selectOptions(screen.getByLabelText("Filter by kind of action"), "person");
    expect(await screen.findByText("Nothing matches")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /clear filters/i }));
    expect(await screen.findByRole("list", { name: "Recorded actions" })).toBeInTheDocument();
    expect(screen.getByLabelText("Filter by kind of action")).toHaveValue("");
  });

  it("says plainly when nothing has been recorded", async () => {
    vi.spyOn(api.audit, "list").mockResolvedValue(page([]));
    renderWithClient(<AuditView />);
    expect(await screen.findByText("Nothing recorded yet")).toBeInTheDocument();
  });

  it("shows the server's reason and offers to try again", async () => {
    const list = vi
      .spyOn(api.audit, "list")
      .mockRejectedValueOnce(new ApiError(500, "internal_error", "Something went wrong on our side."))
      .mockResolvedValue(page([event({ id: "a", action: "auth.login" })]));
    renderWithClient(<AuditView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on our side.");
    await userEvent.setup().click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByRole("list", { name: "Recorded actions" })).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("does not even ask for the log when the person is not an administrator", async () => {
    vi.spyOn(api.auth, "me").mockResolvedValue(employee);
    const list = vi.spyOn(api.audit, "list");
    renderWithClient(<AuditView />);
    expect(await screen.findByText("Only administrators can read the audit log")).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(list).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Filter by kind of action")).not.toBeInTheDocument();
  });

  it("never names a product on the page", async () => {
    vi.spyOn(api.audit, "list").mockResolvedValue(
      page([event({ id: "a", action: "recording.processed", detail: { placeholder: false } })]),
    );
    const { container } = renderWithClient(<AuditView />);
    await screen.findByRole("list", { name: "Recorded actions" });
    expect(container.textContent).not.toMatch(/whisper|pyannote|speechbrain/i);
  });
});
