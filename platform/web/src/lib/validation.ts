import { z } from "zod";

// Client-side validation mirrors the server's rules for fast feedback. The server remains the
// authority: it re-validates every request and these checks are never trusted on their own.

export const emailSchema = z.email("Enter a valid email address.").max(254, "That email address is too long.");

export const passwordSchema = z
  .string()
  .min(12, "Use at least 12 characters.")
  .max(128, "Use 128 characters or fewer.");

export const loginSchema = z.object({
  email: emailSchema,
  password: z.string().min(1, "Enter your password.").max(128),
});

export const registerSchema = z
  .object({
    name: z.string().trim().min(2, "Enter your full name.").max(120),
    password: passwordSchema,
    confirmPassword: z.string(),
  })
  .refine((value) => value.password === value.confirmPassword, {
    path: ["confirmPassword"],
    message: "The passwords do not match.",
  });

export const contactSchema = z.object({
  name: z.string().trim().min(2, "Enter your name.").max(120),
  email: emailSchema,
  company: z.string().trim().max(160).optional(),
  message: z
    .string()
    .trim()
    .min(20, "Tell us a little more (at least 20 characters).")
    .max(4000, "Keep the message under 4000 characters."),
});

export type FieldErrors<T> = Partial<Record<keyof T & string, string>>;

/** Collapse zod issues into one message per field (first issue wins). */
export function toFieldErrors<T>(issues: readonly z.core.$ZodIssue[]): FieldErrors<T> {
  const errors: Record<string, string> = {};
  for (const issue of issues) {
    const key = String(issue.path[0] ?? "");
    if (key && !(key in errors)) errors[key] = issue.message;
  }
  return errors as FieldErrors<T>;
}
