"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { FormAlert } from "@/components/auth/auth-card";
import { PasswordInput } from "@/components/auth/password-input";
import { Button } from "@/components/ui/button";
import { FormField, Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api/client";
import { useLogin } from "@/lib/api/hooks";
import { safeNextPath } from "@/lib/navigation";
import { loginSchema, toFieldErrors, type FieldErrors } from "@/lib/validation";

type Values = { email: string; password: string };

function messageFor(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "The email or password is incorrect.";
    if (error.status === 429) return "Too many attempts. Wait a minute, then try again.";
    return error.message;
  }
  return "Something went wrong. Please try again.";
}

export function LoginForm() {
  const router = useRouter();
  const next = safeNextPath(useSearchParams().get("next"));
  const login = useLogin();
  const [values, setValues] = useState<Values>({ email: "", password: "" });
  const [errors, setErrors] = useState<FieldErrors<Values>>({});

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = loginSchema.safeParse(values);
    if (!parsed.success) {
      setErrors(toFieldErrors<Values>(parsed.error.issues));
      return;
    }
    setErrors({});
    login.mutate(parsed.data, { onSuccess: () => router.replace(next) });
  }

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-6" aria-describedby={login.isError ? "login-error" : undefined}>
      {login.isError ? <FormAlert id="login-error">{messageFor(login.error)}</FormAlert> : null}

      <FormField label="Email" required error={errors.email}>
        {(a11y) => (
          <Input
            {...a11y}
            name="email"
            type="email"
            autoComplete="username"
            inputMode="email"
            autoFocus
            value={values.email}
            onChange={(event) => setValues((v) => ({ ...v, email: event.target.value }))}
          />
        )}
      </FormField>

      <FormField label="Password" required error={errors.password}>
        {(a11y) => (
          <PasswordInput
            {...a11y}
            name="password"
            autoComplete="current-password"
            value={values.password}
            onChange={(event) => setValues((v) => ({ ...v, password: event.target.value }))}
          />
        )}
      </FormField>

      <div className="flex items-center justify-between gap-4">
        <Button type="submit" size="lg" loading={login.isPending}>
          {login.isPending ? "Signing in" : "Sign in"}
        </Button>
        <Link href="/forgot-password" className="text-sm text-fg-muted underline-offset-4 transition-colors hover:text-fg hover:underline">
          Forgot password?
        </Link>
      </div>
    </form>
  );
}
