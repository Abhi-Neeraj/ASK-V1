// Build-time public flags. Only NEXT_PUBLIC_* values are readable in the browser.

/**
 * Demo mode swaps the real API for clearly labelled sample data so the UI can be
 * reviewed without a backend. It is off unless explicitly enabled, and the dashboard
 * shows a persistent banner whenever it is on.
 */
export const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE === "true";

export const SESSION_COOKIE = "ask_session";
export const CSRF_COOKIE = "ask_csrf";
export const CSRF_HEADER = "x-csrf-token";
