import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { AuthCard } from "@/components/auth/auth-card";
import { RegisterFlow } from "@/components/auth/register-flow";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Join your company" };

export default function RegisterPage() {
  return (
    <AuthCard
      title="Join your company"
      description="Accounts are created from an invitation sent by a company administrator."
      footer={
        <p>
          Already have an account?{" "}
          <Link href="/login" className="text-fg underline underline-offset-4">
            Sign in
          </Link>
        </p>
      }
    >
      <Suspense fallback={<Skeleton className="h-40 w-full" />}>
        <RegisterFlow />
      </Suspense>
    </AuthCard>
  );
}
