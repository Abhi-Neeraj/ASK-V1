import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ContactForm } from "@/components/marketing/contact-form";

// These tests stub the network (fetch), so the real API client runs end to end.
const fetchMock = vi.fn<typeof fetch>();

function sentBody(): Record<string, unknown> {
  const [, init] = fetchMock.mock.calls[0];
  return JSON.parse(String((init as RequestInit).body));
}

async function fill(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/^name/i), "Sam Okafor");
  await user.type(screen.getByLabelText(/^work email/i), "sam@example.com");
  await user.type(screen.getByLabelText(/how can we help/i), "We would like a pilot for ten lockets next quarter.");
}

describe("ContactForm", () => {
  beforeEach(() => vi.stubGlobal("fetch", fetchMock));
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("blocks submission and explains each problem", async () => {
    render(<ContactForm />);
    await userEvent.setup().click(screen.getByRole("button", { name: /send message/i }));
    expect(await screen.findByText("Enter your name.")).toBeInTheDocument();
    expect(screen.getByText("Enter a valid email address.")).toBeInTheDocument();
    expect(screen.getByText(/at least 20 characters/i)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends validated data with the honeypot and elapsed time, then confirms", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    render(<ContactForm />);
    const user = userEvent.setup();
    await fill(user);
    await user.click(screen.getByRole("button", { name: /send message/i }));

    expect(await screen.findByRole("status")).toHaveTextContent("Message sent");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/contact");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
    const body = sentBody();
    expect(body).toMatchObject({ name: "Sam Okafor", email: "sam@example.com", website: "" });
    expect(typeof body.elapsedMs).toBe("number");
  });

  it("passes a filled honeypot to the server so it can discard the message", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const { container } = render(<ContactForm />);
    const user = userEvent.setup();
    await fill(user);
    const trap = container.querySelector<HTMLInputElement>('input[name="website"]')!;
    expect(trap).toHaveAttribute("tabindex", "-1");
    fireEvent.change(trap, { target: { value: "https://spam.example" } });
    await user.click(screen.getByRole("button", { name: /send message/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(sentBody().website).toBe("https://spam.example");
  });

  it("maps server-side validation errors onto the fields", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          error: { code: "validation_error", message: "Invalid.", details: { email: "That domain cannot receive mail." } },
        }),
        { status: 422 },
      ),
    );
    render(<ContactForm />);
    const user = userEvent.setup();
    await fill(user);
    await user.click(screen.getByRole("button", { name: /send message/i }));
    expect(await screen.findByText("That domain cannot receive mail.")).toBeInTheDocument();
    expect(screen.getByText("Please fix the highlighted fields.")).toBeInTheDocument();
  });

  it("keeps the form filled and shows the problem when the server is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    render(<ContactForm />);
    const user = userEvent.setup();
    await fill(user);
    await user.click(screen.getByRole("button", { name: /send message/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/can't reach the ask server/i);
    expect(screen.getByLabelText(/^name/i)).toHaveValue("Sam Okafor");
  });
});
