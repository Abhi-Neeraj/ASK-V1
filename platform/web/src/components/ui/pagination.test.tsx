import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Pagination } from "@/components/ui/pagination";

describe("Pagination", () => {
  it("says which rows are showing and which page this is", () => {
    render(<Pagination page={2} pageSize={8} total={12} onPage={() => {}} />);
    expect(screen.getByText("Showing 9–12 of 12")).toBeInTheDocument();
    expect(screen.getByText("Page 2 of 2")).toBeInTheDocument();
  });

  it("cannot go before the first page or past the last", () => {
    const { rerender } = render(<Pagination page={1} pageSize={8} total={12} onPage={() => {}} />);
    expect(screen.getByRole("button", { name: /previous/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /next/i })).toBeEnabled();
    rerender(<Pagination page={2} pageSize={8} total={12} onPage={() => {}} />);
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
  });

  it("asks for the neighbouring page", async () => {
    const onPage = vi.fn();
    const user = userEvent.setup();
    render(<Pagination page={2} pageSize={8} total={30} onPage={onPage} />);
    await user.click(screen.getByRole("button", { name: /previous/i }));
    await user.click(screen.getByRole("button", { name: /next/i }));
    expect(onPage.mock.calls).toEqual([[1], [3]]);
  });

  it("copes with nothing to show", () => {
    render(<Pagination page={1} pageSize={8} total={0} onPage={() => {}} />);
    expect(screen.getByText("Showing 0–0 of 0")).toBeInTheDocument();
    expect(screen.getByText("Page 1 of 1")).toBeInTheDocument();
  });
});
