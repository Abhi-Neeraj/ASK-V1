import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { AuthCard } from "@/components/auth/auth-card";
import { LoginForm } from "@/components/auth/login-form";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    <AuthCard
      title="Sign in"
      description="Use your company account to manage lockets, recordings and people."
      footer={
        <p>
          Joining a company? Accept the invitation link your administrator sent you, or{" "}
          <Link href="/register" className="text-fg underline underline-offset-4">
            learn how registration works
          </Link>
          .
        </p>
      }
    >
      {/* LoginForm reads ?next=, which needs a Suspense boundary under Cache Components. */}
      <Suspense fallback={<LoginFallback />}>
        <LoginForm />
      </Suspense>
    </AuthCard>
  );
}

function LoginFallback() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading sign-in form">
      <Skeleton className="h-11 w-full" />
      <Skeleton className="h-11 w-full" />
      <Skeleton className="h-11 w-32 rounded-full" />
    </div>
  );
}
