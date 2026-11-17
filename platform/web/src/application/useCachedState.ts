import React from "react";
import { readCache, writeCache } from "@/application/cache";
import { useSession } from "@/application/session";

/**
 * State that starts from the last answer the server gave for `name` (this person, this tab) and remembers each new
 * one. A screen uses it exactly like useState: it paints the remembered data at once, loads fresh data, and sets it.
 * With `persist: false` the answer is kept in memory only (it survives moving around the app, not a reload): used
 * for transcripts and summaries.
 */
export function useCachedState<T>(
  name: string,
  initial: T,
  { persist = true }: { persist?: boolean } = {},
): [T, React.Dispatch<React.SetStateAction<T>>] {
  const { user } = useSession();
  const key = `${user?.id ?? "-"}:${name}`;
  const [value, setValue] = React.useState<T>(() => readCache<T>(key) ?? initial);
  const first = React.useRef(true);
  React.useEffect(() => {
    // The value on the first render is the remembered one (or the empty start): nothing new to remember yet.
    if (first.current) {
      first.current = false;
      return;
    }
    if (value !== null && value !== undefined) writeCache(key, value, persist);
  }, [key, value, persist]);
  return [value, setValue];
}
