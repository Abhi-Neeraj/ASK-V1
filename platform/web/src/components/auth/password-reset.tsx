"use client";

import { useMutation } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { FormAlert } from "@/components/auth/auth-card";
import { PasswordInput } from "@/components/auth/password-input";
import { Button } from "@/components/ui/button";
import { FormField, Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import { emailSchema, passwordSchema } from "@/lib/validation";

/**
 * Asks for a reset link. The confirmation is the same whether or not the address belongs to an
 * account, so this form cannot be used to discover which emails are registered.
 */
export function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | undefined>();
  const request = useMutation({ mutationFn: (value: string) => api.auth.requestPasswordReset(value) });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = emailSchema.safeParse(email);
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message);
      return;
    }
    setError(undefined);
    request.mutate(parsed.data);
  }

  if (request.isSuccess) {
    return (
      <div role="status" className="space-y-4 border border-success/40 bg-success/[0.06] p-6">
        <CheckCircle2 className="size-6 text-success" aria-hidden="true" />
        <p className="text-sm leading-relaxed text-fg-muted">
          If an account exists for that email address, we have sent a link to reset the password. The link
          expires shortly, so use it soon.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-6" aria-describedby={request.isError ? "forgot-error" : undefined}>
      {request.isError ? (
        <FormAlert id="forgot-error">
          {request.error instanceof ApiError ? request.error.message : "Something went wrong. Please try again."}
        </FormAlert>
      ) : null}
      <FormField label="Email" required error={error}>
        {(a11y) => (
          <Input
            {...a11y}
            name="email"
            type="email"
            autoComplete="email"
            inputMode="email"
            autoFocus
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        )}
      </FormField>
      <Button type="submit" size="lg" loading={request.isPending}>
        {request.isPending ? "Sending" : "Send reset link"}
      </Button>
    </form>
  );
}

export function ResetPasswordForm() {
  const token = useSearchParams().get("token");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<{ password?: string; confirm?: string }>({});
  const reset = useMutation({ mutationFn: (value: string) => api.auth.resetPassword(token ?? "", value) });

  if (!token) {
    return (
      <FormAlert id="reset-no-token">
        This reset link is incomplete. Request a new one from the{" "}
        <Link href="/forgot-password" className="underline underline-offset-4">
          forgot password page
        </Link>
        .
      </FormAlert>
    );
  }

  if (reset.isSuccess) {
    return (
      <div role="status" className="space-y-4 border border-success/40 bg-success/[0.06] p-6">
        <CheckCircle2 className="size-6 text-success" aria-hidden="true" />
        <p className="text-sm leading-relaxed text-fg-muted">Your password has been changed. You can sign in now.</p>
        <Button asChild size="sm">
          <Link href="/login">Sign in</Link>
        </Button>
      </div>
    );
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = passwordSchema.safeParse(password);
    const next: { password?: string; confirm?: string } = {};
    if (!parsed.success) next.password = parsed.error.issues[0]?.message;
    if (password !== confirm) next.confirm = "The passwords do not match.";
    setErrors(next);
    if (Object.keys(next).length === 0) reset.mutate(password);
  }

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-6" aria-describedby={reset.isError ? "reset-error" : undefined}>
      {reset.isError ? (
        <FormAlert id="reset-error">
          {reset.error instanceof ApiError && reset.error.status < 500
            ? "This reset link is invalid or has expired. Request a new one."
            : "Something went wrong. Please try again."}
        </FormAlert>
      ) : null}
      <FormField label="New password" required hint="At least 12 characters." error={errors.password}>
        {(a11y) => (
          <PasswordInput
            {...a11y}
            name="new-password"
            autoComplete="new-password"
            autoFocus
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        )}
      </FormField>
      <FormField label="Confirm new password" required error={errors.confirm}>
        {(a11y) => (
          <PasswordInput
            {...a11y}
            name="confirm-password"
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
          />
        )}
      </FormField>
      <Button type="submit" size="lg" loading={reset.isPending}>
        {reset.isPending ? "Saving" : "Set new password"}
      </Button>
    </form>
  );
}
