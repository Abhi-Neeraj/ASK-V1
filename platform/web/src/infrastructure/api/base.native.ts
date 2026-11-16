// Where the ASK server is, for the phone app: EXPO_PUBLIC_API_URL in platform/web/.env, for example
// http://192.168.0.10:8000 (the computer running the server, on the same Wi-Fi). The website uses base.ts.
export function apiBase(): string {
  const configured = process.env.EXPO_PUBLIC_API_URL ?? "";
  return configured.replace(/\/+$/, "");
}
