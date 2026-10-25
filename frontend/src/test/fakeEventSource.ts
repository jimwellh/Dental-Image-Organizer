import type { EventSourceLike } from "../api/jobs";
import type { JobState, JobView } from "../api/types";
import { jobView } from "./records";

type Listener = (event: MessageEvent<string>) => void;

/**
 * Stands in for the browser's `EventSource`, which jsdom lacks. `setup.ts` installs it for every
 * test; a test drives the stream through `FakeEventSource.last`.
 */
export class FakeEventSource implements EventSourceLike {
  static last: FakeEventSource | null = null;

  readonly url: string;
  readyState = 1;
  closed = false;
  onerror: ((event: Event) => void) | null = null;
  private readonly listeners = new Map<string, Listener[]>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.last = this;
  }

  addEventListener(type: string, listener: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.closed = true;
    this.readyState = 2;
  }

  /** Sends one event for the job named in the URL, as the server does on each state change. */
  emit(state: JobState, job: Partial<JobView> = {}): void {
    // A closed `EventSource` delivers nothing.
    if (this.closed) return;
    const jobId = /\/jobs\/([^/]+)\/events/.exec(this.url)?.[1];
    if (jobId === undefined) throw new Error(`Not a job events URL: ${this.url}`);
    const data = JSON.stringify(jobView({ id: jobId, state, ...job }));
    for (const listener of this.listeners.get(state) ?? []) {
      listener(new MessageEvent(state, { data }));
    }
  }

  /** 0: the browser is reconnecting. 2: the server refused the stream and the browser gave up. */
  fail(readyState: 0 | 2): void {
    this.readyState = readyState;
    this.onerror?.(new Event("error"));
  }
}
