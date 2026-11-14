import { ArrowRight } from "lucide-react";
import { Fragment } from "react";

const MAIN = ["Uploaded", "Queued", "Validating", "Transcribing", "Separating speakers", "Identifying speakers", "Aligning", "Completed"];

/** The processing state machine as a picture: the happy path on top, the side states below. */
export function PipelineStates() {
  return (
    <figure>
      <div role="img" aria-label="Processing states: uploaded, queued, validating, transcribing, separating speakers, identifying speakers, aligning, completed. Side states: retrying, needs review, failed.">
        <ol aria-hidden="true" className="flex flex-wrap items-center gap-x-2 gap-y-3">
          {MAIN.map((state, index) => (
            <Fragment key={state}>
              <li
                className={
                  index === MAIN.length - 1
                    ? "rounded-full border border-success/45 bg-success/10 px-3.5 py-1.5 text-xs text-success"
                    : "rounded-full border border-cerulean/45 bg-cerulean/10 px-3.5 py-1.5 text-xs text-cerulean-glow"
                }
              >
                {state}
              </li>
              {index < MAIN.length - 1 ? <ArrowRight className="size-3.5 text-fg-subtle" /> : null}
            </Fragment>
          ))}
        </ol>
        <div aria-hidden="true" className="mt-5 flex flex-wrap gap-3 border-t border-line pt-5 text-xs">
          <span className="rounded-full border border-warning/40 bg-warning/10 px-3.5 py-1.5 text-warning">Retrying</span>
          <span className="rounded-full border border-rose/45 bg-rose/10 px-3.5 py-1.5 text-rose-soft">Needs review</span>
          <span className="rounded-full border border-danger/45 bg-danger/10 px-3.5 py-1.5 text-danger">Failed</span>
        </div>
      </div>
    </figure>
  );
}
