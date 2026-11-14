import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/env";

/**
 * Optimistic gate for /dashboard: no session cookie means no point rendering the shell, so go to
 * sign-in. This is NOT authorization. The cookie is only checked for presence here; the API
 * validates the session and authorizes every request on its own.
 */
export function proxy(request: NextRequest) {
  // Demo builds have no backend and no session cookie.
  if (process.env.NEXT_PUBLIC_DEMO_MODE === "true") return NextResponse.next();

  if (!request.cookies.has(SESSION_COOKIE)) {
    const url = new URL("/login", request.url);
    url.searchParams.set("next", request.nextUrl.pathname + request.nextUrl.search);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/dashboard/:path*"],
};
