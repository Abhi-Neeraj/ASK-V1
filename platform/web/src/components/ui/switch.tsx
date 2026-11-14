import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";

/** Native checkbox exposed as a switch: keyboard and form behavior come for free. */
export function Switch({ className, ...props }: Omit<ComponentProps<"input">, "type" | "role">) {
  return (
    <input
      type="checkbox"
      role="switch"
      className={cn(
        "relative h-6 w-11 shrink-0 cursor-pointer appearance-none rounded-full border border-line-strong bg-white/10 transition-colors",
        "before:absolute before:left-0.5 before:top-0.5 before:size-4.5 before:rounded-full before:bg-white before:transition-transform",
        "checked:border-cerulean checked:bg-cerulean checked:before:translate-x-5",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}
