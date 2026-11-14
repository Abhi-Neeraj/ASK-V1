"use client";

import { CheckCircle2 } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { FormField, Input, Textarea } from "@/components/ui/input";
import { ApiError, apiFetch } from "@/lib/api/client";
import { contactSchema, toFieldErrors, type FieldErrors } from "@/lib/validation";

type Values = { name: string; email: string; company: string; message: string };
type Status = "idle" | "submitting" | "sent" | "error";

const EMPTY: Values = { name: "", email: "", company: "", message: "" };

/**
 * Spam protection on the client side: a honeypot field real people never see, and the time
 * since the form was shown (bots submit instantly). The server enforces both again, plus
 * rate limiting. None of this is a CAPTCHA; it is a first layer.
 */
export function ContactForm() {
  const [values, setValues] = useState<Values>(EMPTY);
  const [errors, setErrors] = useState<FieldErrors<Values>>({});
  const [status, setStatus] = useState<Status>("idle");
  const [formError, setFormError] = useState<string | null>(null);
  const shownAt = useRef<number | null>(null);
  const honeypot = useRef<HTMLInputElement>(null);

  useEffect(() => {
    shownAt.current = Date.now();
  }, []);

  const update = (key: keyof Values) => (event: { target: { value: string } }) =>
    setValues((current) => ({ ...current, [key]: event.target.value }));

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);

    const parsed = contactSchema.safeParse(values);
    if (!parsed.success) {
      setErrors(toFieldErrors<Values>(parsed.error.issues));
      return;
    }
    setErrors({});
    setStatus("submitting");

    try {
      await apiFetch<void>("/contact", {
        method: "POST",
        json: {
          ...parsed.data,
          website: honeypot.current?.value ?? "",
          elapsedMs: shownAt.current ? Date.now() - shownAt.current : 0,
        },
      });
      setValues(EMPTY);
      setStatus("sent");
    } catch (error) {
      setStatus("error");
      if (error instanceof ApiError && error.code === "validation_error" && error.details) {
        setErrors(error.details as FieldErrors<Values>);
        setFormError("Please fix the highlighted fields.");
      } else {
        setFormError(error instanceof ApiError ? error.message : "Something went wrong. Please try again.");
      }
    }
  }

  if (status === "sent") {
    return (
      <div role="status" className="flex flex-col items-start gap-4 border border-success/40 bg-success/[0.06] p-8">
        <CheckCircle2 className="size-7 text-success" aria-hidden="true" />
        <h2 className="font-heading text-xl font-semibold text-fg">Message sent</h2>
        <p className="text-sm leading-relaxed text-fg-muted">
          Thanks for getting in touch. We will reply to the email address you provided.
        </p>
        <Button variant="secondary" size="sm" onClick={() => setStatus("idle")}>
          Send another message
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-6" aria-describedby={formError ? "contact-form-error" : undefined}>
      {formError ? (
        <p id="contact-form-error" role="alert" className="border border-danger/40 bg-danger/[0.08] px-4 py-3 text-sm text-danger">
          {formError}
        </p>
      ) : null}

      <div className="grid gap-6 sm:grid-cols-2">
        <FormField label="Name" required error={errors.name}>
          {(a11y) => (
            <Input {...a11y} name="name" autoComplete="name" value={values.name} onChange={update("name")} />
          )}
        </FormField>
        <FormField label="Work email" required error={errors.email}>
          {(a11y) => (
            <Input
              {...a11y}
              name="email"
              type="email"
              autoComplete="email"
              inputMode="email"
              value={values.email}
              onChange={update("email")}
            />
          )}
        </FormField>
      </div>

      <FormField label="Company" hint="Optional" error={errors.company}>
        {(a11y) => (
          <Input {...a11y} name="company" autoComplete="organization" value={values.company} onChange={update("company")} />
        )}
      </FormField>

      <FormField label="How can we help?" required error={errors.message}>
        {(a11y) => <Textarea {...a11y} name="message" rows={6} value={values.message} onChange={update("message")} />}
      </FormField>

      {/* Honeypot: hidden from people and assistive tech, bots tend to fill it. */}
      <div aria-hidden="true" className="absolute -left-[9999px] top-auto h-px w-px overflow-hidden">
        <label>
          Leave this field empty
          <input ref={honeypot} type="text" name="website" tabIndex={-1} autoComplete="off" />
        </label>
      </div>

      <Button type="submit" size="lg" loading={status === "submitting"}>
        {status === "submitting" ? "Sending" : "Send message"}
      </Button>
    </form>
  );
}
