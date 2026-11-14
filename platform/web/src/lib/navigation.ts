/**
 * Only same-site paths are allowed as a post-login destination. Anything else (absolute URLs,
 * protocol-relative "//host", backslash tricks) falls back to the dashboard, which prevents
 * the login page from being used as an open redirect.
 */
export function safeNextPath(value: string | null | undefined, fallback = "/dashboard"): string {
  if (!value) return fallback;
  if (!value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return fallback;
  try {
    const url = new URL(value, "http://placeholder.local");
    if (url.origin !== "http://placeholder.local") return fallback;
  } catch {
    return fallback;
  }
  return value;
}
