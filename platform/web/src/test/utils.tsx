import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import axe from "axe-core";
import type { ReactElement } from "react";

/** Fresh query client per render: no retries, no cache shared between tests. */
export function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  const result = render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  return { ...result, client };
}

/** Marks the demo session as signed in, the way the demo login does. */
export function signInDemo() {
  window.localStorage.setItem("ask_demo_session", "1");
}

/**
 * Runs axe-core against a rendered subtree. Color contrast is skipped because jsdom has no
 * layout or computed styles; contrast is checked by hand against the design tokens instead.
 */
export async function expectNoA11yViolations(container: HTMLElement) {
  // `region` only makes sense for whole pages, not for isolated components.
  const results = await axe.run(container, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
  const summary = results.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.length} node(s))`);
  if (summary.length > 0) throw new Error(`Accessibility violations:\n${summary.join("\n")}`);
}
