"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MailWarning } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { FormAlert } from "@/components/auth/auth-card";
import { PasswordInput } from "@/components/auth/password-input";
import { Button } from "@/components/ui/button";
import { FormField, Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/states";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/hooks";
import { registerSchema, toFieldErrors, type FieldErrors } from "@/lib/validation";

/**
 * Registration is invitation-only. The token in the link identifies a specific invitation
 * (company, email address and role) created by an administrator. Knowing a company's email
 * domain is never enough to join it.
 */
export function RegisterFlow() {
  const token = useSearchParams().get("token");
  return token ? <AcceptInvitation token={token} /> : <NoInvitation />;
}

function NoInvitation() {
  return (
    <div className="space-y-5 border border-line-strong p-7">
      <MailWarning className="size-6 text-cerulean-glow" aria-hidden="true" />
      <h2 className="font-heading text-lg font-semibold text-fg">You need an invitation</h2>
      <p className="text-sm leading-relaxed text-fg-muted">
        A company administrator adds people by email. Open the link in that invitation to create your account.
        If you did not receive one, ask your administrator to send a new invitation.
      </p>
      <div className="flex flex-wrap gap-3">
        <Button asChild size="sm">
          <Link href="/login">Sign in</Link>
        </Button>
        <Button asChild variant="secondary" size="sm">
          <Link href="/contact">Contact us</Link>
        </Button>
      </div>
    </div>
  );
}

type Values = { name: string; password: string; confirmPassword: string };

function AcceptInvitation({ token }: { token: string }) {
  const router = useRouter();
  const client = useQueryClient();
  const invitation = useQuery({
    queryKey: ["invitation", token],
    queryFn: () => api.auth.invitation(token),
    retry: false,
  });
  const accept = useMutation({
    mutationFn: (input: { name: string; password: string }) => api.auth.acceptInvitation(token, input),
    onSuccess: (user) => {
      client.setQueryData(queryKeys.me, user);
      router.replace("/dashboard");
    },
  });
  const [values, setValues] = useState<Values>({ name: "", password: "", confirmPassword: "" });
  const [errors, setErrors] = useState<FieldErrors<Values>>({});

  if (invitation.isPending) {
    return (
      <div className="space-y-6" aria-busy="true" aria-label="Checking your invitation">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-11 w-full" />
        <Skeleton className="h-11 w-full" />
      </div>
    );
  }

  if (invitation.isError) {
    const gone = invitation.error instanceof ApiError && invitation.error.status < 500 && !invitation.error.isNetwork;
    return (
      <ErrorState
        title={gone ? "This invitation is not valid" : "We could not check your invitation"}
        description={
          gone
            ? "The link may have expired or already been used. Ask your administrator for a new invitation."
            : invitation.error.message
        }
        onRetry={gone ? undefined : () => invitation.refetch()}
      />
    );
  }

  const info = invitation.data;

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = registerSchema.safeParse(values);
    if (!parsed.success) {
      setErrors(toFieldErrors<Values>(parsed.error.issues));
      return;
    }
    setErrors({});
    accept.mutate({ name: parsed.data.name, password: parsed.data.password });
  }

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-6" aria-describedby={accept.isError ? "register-error" : undefined}>
      <div className="border border-cerulean/35 bg-cerulean/[0.06] p-5 text-sm">
        <p className="caption text-cerulean-glow">You are joining</p>
        <p className="mt-2 text-base font-medium text-fg">{info.companyName}</p>
        <p className="mt-1 text-fg-muted">
          as {info.role} · {info.email}
        </p>
      </div>

      {accept.isError ? (
        <FormAlert id="register-error">
          {accept.error instanceof ApiError ? accept.error.message : "Something went wrong. Please try again."}
        </FormAlert>
      ) : null}

      <FormField label="Full name" required error={errors.name}>
        {(a11y) => (
          <Input
            {...a11y}
            name="name"
            autoComplete="name"
            autoFocus
            value={values.name}
            onChange={(event) => setValues((v) => ({ ...v, name: event.target.value }))}
          />
        )}
      </FormField>

      <FormField label="Password" required hint="At least 12 characters." error={errors.password}>
        {(a11y) => (
          <PasswordInput
            {...a11y}
            name="new-password"
            autoComplete="new-password"
            value={values.password}
            onChange={(event) => setValues((v) => ({ ...v, password: event.target.value }))}
          />
        )}
      </FormField>

      <FormField label="Confirm password" required error={errors.confirmPassword}>
        {(a11y) => (
          <PasswordInput
            {...a11y}
            name="confirm-password"
            autoComplete="new-password"
            value={values.confirmPassword}
            onChange={(event) => setValues((v) => ({ ...v, confirmPassword: event.target.value }))}
          />
        )}
      </FormField>

      <Button type="submit" size="lg" loading={accept.isPending}>
        {accept.isPending ? "Creating account" : "Create account"}
      </Button>
    </form>
  );
}
