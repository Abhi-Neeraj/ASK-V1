import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LoginForm } from "@/components/auth/login-form";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import { demoUser } from "@/lib/demo/data";
import { renderWithClient } from "@/test/utils";

const nav = vi.hoisted(() => ({ replace: vi.fn(), search: "" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace }),
  useSearchParams: () => new URLSearchParams(nav.search),
}));

async function fillAndSubmit(user: ReturnType<typeof userEvent.setup>, email = "demo@example.com", password = "pw") {
  await user.type(screen.getByLabelText(/^email/i), email);
  await user.type(screen.getByLabelText(/^password/i), password);
  await user.click(screen.getByRole("button", { name: /^sign in$/i }));
}

describe("LoginForm", () => {
  beforeEach(() => {
    nav.replace.mockReset();
    nav.search = "";
    vi.restoreAllMocks();
  });

  it("shows field errors and does not call the API when the form is empty", async () => {
    const login = vi.spyOn(api.auth, "login");
    renderWithClient(<LoginForm />);
    await userEvent.setup().click(screen.getByRole("button", { name: /^sign in$/i }));
    expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
    expect(login).not.toHaveBeenCalled();
  });

  it("signs in and goes to the dashboard by default", async () => {
    vi.spyOn(api.auth, "login").mockResolvedValue(demoUser);
    renderWithClient(<LoginForm />);
    await fillAndSubmit(userEvent.setup());
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/dashboard"));
  });

  it("returns to the page the user came from", async () => {
    nav.search = "next=%2Fdashboard%2Fdevices";
    vi.spyOn(api.auth, "login").mockResolvedValue(demoUser);
    renderWithClient(<LoginForm />);
    await fillAndSubmit(userEvent.setup());
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/dashboard/devices"));
  });

  it("ignores an off-site next parameter (no open redirect)", async () => {
    nav.search = "next=https%3A%2F%2Fevil.example";
    vi.spyOn(api.auth, "login").mockResolvedValue(demoUser);
    renderWithClient(<LoginForm />);
    await fillAndSubmit(userEvent.setup());
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/dashboard"));
  });

  it("gives one generic message for wrong credentials", async () => {
    vi.spyOn(api.auth, "login").mockRejectedValue(new ApiError(401, "invalid_credentials", "No such user"));
    renderWithClient(<LoginForm />);
    await fillAndSubmit(userEvent.setup());
    expect(await screen.findByRole("alert")).toHaveTextContent("The email or password is incorrect.");
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("explains rate limiting", async () => {
    vi.spyOn(api.auth, "login").mockRejectedValue(new ApiError(429, "rate_limited", "Slow down"));
    renderWithClient(<LoginForm />);
    await fillAndSubmit(userEvent.setup());
    expect(await screen.findByRole("alert")).toHaveTextContent(/too many attempts/i);
  });

  it("shows a loading state and blocks double submits", async () => {
    let finish: (user: typeof demoUser) => void = () => {};
    const login = vi.spyOn(api.auth, "login").mockReturnValue(new Promise((resolve) => (finish = resolve)));
    renderWithClient(<LoginForm />);
    await fillAndSubmit(userEvent.setup());
    const button = await screen.findByRole("button", { name: /signing in/i });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    finish(demoUser);
    await waitFor(() => expect(login).toHaveBeenCalledTimes(1));
  });

  it("can reveal and hide the password", async () => {
    renderWithClient(<LoginForm />);
    const user = userEvent.setup();
    const field = screen.getByLabelText(/^password/i);
    expect(field).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "Show password" }));
    expect(field).toHaveAttribute("type", "text");
    await user.click(screen.getByRole("button", { name: "Hide password" }));
    expect(field).toHaveAttribute("type", "password");
  });
});
