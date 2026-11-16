// Where the ASK server is. On the web the app is served by serve.mjs, which forwards /api to the server, so the
// base is always "" (same origin, so the sign-in cookie works). EXPO_PUBLIC_API_URL in
// platform/web/.env is for the phone app only (base.native.ts).
export function apiBase(): string {
  return "";
}
