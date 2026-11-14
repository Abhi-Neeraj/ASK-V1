import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FormField, Input } from "@/components/ui/input";

describe("FormField", () => {
  it("links the label, hint and control", () => {
    render(
      <FormField label="Work email" hint="We never share it.">
        {(a11y) => <Input {...a11y} />}
      </FormField>,
    );
    const input = screen.getByLabelText("Work email");
    expect(input).not.toHaveAttribute("aria-invalid");
    const hint = screen.getByText("We never share it.");
    expect(input).toHaveAttribute("aria-describedby", hint.id);
  });

  it("announces an error and marks the control invalid, replacing the hint", () => {
    render(
      <FormField label="Name" hint="Your full name" error="Enter your name.">
        {(a11y) => <Input {...a11y} />}
      </FormField>,
    );
    const input = screen.getByLabelText("Name");
    expect(input).toHaveAttribute("aria-invalid", "true");
    const error = screen.getByRole("alert");
    expect(error).toHaveTextContent("Enter your name.");
    expect(input).toHaveAttribute("aria-describedby", error.id);
    expect(screen.queryByText("Your full name")).not.toBeInTheDocument();
  });

  it("marks required fields for assistive tech and sighted users", () => {
    render(
      <FormField label="Email" required>
        {(a11y) => <Input {...a11y} />}
      </FormField>,
    );
    expect(screen.getByRole("textbox", { name: /email/i })).toHaveAttribute("aria-required", "true");
  });
});
