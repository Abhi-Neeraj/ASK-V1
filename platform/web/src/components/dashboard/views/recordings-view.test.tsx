import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RecordingsView } from "@/components/dashboard/views/recordings-view";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import { renderWithClient, signInDemo } from "@/test/utils";

describe("RecordingsView (demo API)", () => {
  beforeEach(() => {
    signInDemo();
    vi.restoreAllMocks();
  });

  it("lists recordings with their status and the total count", async () => {
    renderWithClient(<RecordingsView />);
    expect(await screen.findByText(/confirm the delivery window/i)).toBeInTheDocument();
    expect(screen.getByText("Showing 1–8 of 12")).toBeInTheDocument();
    expect(screen.getByText("Page 1 of 2")).toBeInTheDocument();
    const list = within(screen.getByRole("list"));
    expect(list.getAllByText("Completed").length).toBeGreaterThan(0);
    expect(list.getByText("Needs review")).toBeInTheDocument();
  });

  it("links each recording to its detail page", async () => {
    renderWithClient(<RecordingsView />);
    const link = (await screen.findByText(/confirm the delivery window/i)).closest("a");
    expect(link).toHaveAttribute("href", "/dashboard/recordings/rec_001");
  });

  it("searches transcript text", async () => {
    renderWithClient(<RecordingsView />);
    await screen.findByText(/confirm the delivery window/i);
    await userEvent.setup().type(screen.getByRole("searchbox", { name: /search transcripts/i }), "purchase order");
    expect(await screen.findByText(/check the purchase order status/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/confirm the delivery window/i)).not.toBeInTheDocument());
    expect(screen.getByText("Showing 1–1 of 1")).toBeInTheDocument();
  });

  it("shows a helpful empty state when nothing matches, and clears the filters", async () => {
    renderWithClient(<RecordingsView />);
    const user = userEvent.setup();
    await screen.findByText(/confirm the delivery window/i);
    await user.type(screen.getByRole("searchbox", { name: /search transcripts/i }), "zzzz-no-match");
    expect(await screen.findByText("Nothing matches")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /clear filters/i }));
    expect(await screen.findByText(/confirm the delivery window/i)).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: /search transcripts/i })).toHaveValue("");
  });

  it("filters by processing status", async () => {
    renderWithClient(<RecordingsView />);
    await screen.findByText(/confirm the delivery window/i);
    await userEvent.setup().selectOptions(screen.getByLabelText("Filter by status"), "failed");
    // The previous page stays on screen until the filtered results arrive, so wait for the count.
    expect(await screen.findByText("Showing 1–1 of 1")).toBeInTheDocument();
    expect(screen.getByText(/could not be decoded/i)).toBeInTheDocument();
    expect(screen.queryByText(/confirm the delivery window/i)).not.toBeInTheDocument();
  });

  it("paginates, and returns to page one when a filter changes", async () => {
    renderWithClient(<RecordingsView />);
    const user = userEvent.setup();
    await screen.findByText(/confirm the delivery window/i);
    await user.click(screen.getByRole("button", { name: /next/i }));
    expect(await screen.findByText("Page 2 of 2")).toBeInTheDocument();
    expect(screen.getByText("Showing 9–12 of 12")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();

    await user.selectOptions(screen.getByLabelText("Filter by status"), "completed");
    expect(await screen.findByText(/Page 1 of/)).toBeInTheDocument();
  });

  it("shows an error with a retry when the API fails", async () => {
    const list = vi.spyOn(api.recordings, "list").mockRejectedValueOnce(new ApiError(500, "server_error", "The server had a problem."));
    renderWithClient(<RecordingsView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The server had a problem.");
    await userEvent.setup().click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByText(/confirm the delivery window/i)).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("shows an empty library message before any recording exists", async () => {
    vi.spyOn(api.recordings, "list").mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 8 });
    renderWithClient(<RecordingsView />);
    expect(await screen.findByText("No recordings yet")).toBeInTheDocument();
  });
});
