import { describe, expect, it } from "vitest";
import { contactSchema, loginSchema, registerSchema, toFieldErrors } from "@/lib/validation";

describe("loginSchema", () => {
  it("accepts a valid email and a password", () => {
    expect(loginSchema.safeParse({ email: "a@b.co", password: "x" }).success).toBe(true);
  });
  it("rejects a malformed email and an empty password", () => {
    const result = loginSchema.safeParse({ email: "not-an-email", password: "" });
    expect(result.success).toBe(false);
    if (!result.success) {
      const errors = toFieldErrors<{ email: string; password: string }>(result.error.issues);
      expect(errors.email).toBe("Enter a valid email address.");
      expect(errors.password).toBe("Enter your password.");
    }
  });
});

describe("registerSchema", () => {
  const base = { name: "Priya Nair", password: "a-long-enough-password", confirmPassword: "a-long-enough-password" };
  it("accepts matching passwords of 12+ characters", () => {
    expect(registerSchema.safeParse(base).success).toBe(true);
  });
  it("rejects short passwords", () => {
    expect(registerSchema.safeParse({ ...base, password: "short", confirmPassword: "short" }).success).toBe(false);
  });
  it("reports a mismatch on the confirm field", () => {
    const result = registerSchema.safeParse({ ...base, confirmPassword: "different-password!" });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(toFieldErrors<{ confirmPassword: string }>(result.error.issues).confirmPassword).toBe(
        "The passwords do not match.",
      );
    }
  });
});

describe("contactSchema", () => {
  const valid = { name: "Sam", email: "sam@example.com", message: "We would like a pilot for ten lockets." };
  it("accepts a normal message", () => {
    expect(contactSchema.safeParse(valid).success).toBe(true);
  });
  it("requires a meaningful message and trims whitespace", () => {
    expect(contactSchema.safeParse({ ...valid, message: "   hi   " }).success).toBe(false);
    const parsed = contactSchema.safeParse({ ...valid, name: "  Sam  " });
    expect(parsed.success && parsed.data.name).toBe("Sam");
  });
  it("caps message length", () => {
    expect(contactSchema.safeParse({ ...valid, message: "x".repeat(4001) }).success).toBe(false);
  });
});
