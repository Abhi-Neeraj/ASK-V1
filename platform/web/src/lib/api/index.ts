import type { AskApi } from "@/lib/api/contract";
import { demoApi } from "@/lib/demo/api";
import { realApi } from "@/lib/api/real";
import { DEMO_MODE } from "@/lib/env";

/** The backend the UI talks to. Demo mode is opt-in at build time and always labelled in the UI. */
export const api: AskApi = DEMO_MODE ? demoApi : realApi;
