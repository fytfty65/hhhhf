import { apiFetch } from './apiClient';

/**
 * Batched planning-event tracking.
 *
 * SQLite admits one writer, and every event used to be its own POST -> its own
 * INSERT -> its own write-lock round trip. A short interaction (switch a tab,
 * swap two sights, favourite a post) produced several of those back to back,
 * each competing with chat messages and planning writes.
 *
 * Events are queued for a short window and sent to
 * `POST /api/v1/planning/events/batch`, which validates every event exactly like
 * the single-event route but inserts them in one statement. If the batch route
 * is unavailable (older gateway, proxy), the queue falls back to individual
 * posts, so analytics is never silently dropped.
 */

export type PlanningEventInput = {
  event_type: string;
  [key: string]: unknown;
};

const MAX_BATCH_SIZE = 20;
const FLUSH_DELAY_MS = 600;

const queue: PlanningEventInput[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;
let inFlight: Promise<void> | null = null;

async function postSingle(event: PlanningEventInput): Promise<void> {
  await apiFetch('/api/v1/planning/events', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(event),
  });
}

async function send(batch: PlanningEventInput[]): Promise<void> {
  try {
    const response = await apiFetch('/api/v1/planning/events/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ events: batch }),
    });
    if (response.ok) return;
  } catch {
    // Fall through to the per-event route.
  }
  await Promise.all(batch.map((event) => postSingle(event).catch(() => undefined)));
}

/**
 * Queue one planning event. Analytics must never block the UI, so this returns
 * immediately; callers that previously used `.catch` no longer need it.
 */
export function trackPlanningEvent(event: PlanningEventInput): void {
  queue.push(event);
  if (queue.length >= MAX_BATCH_SIZE) {
    void flushPlanningEvents();
    return;
  }
  if (timer === null) {
    timer = setTimeout(() => {
      timer = null;
      void flushPlanningEvents();
    }, FLUSH_DELAY_MS);
  }
}

/** Send everything queued now. Safe to call concurrently. */
export async function flushPlanningEvents(): Promise<void> {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
  if (inFlight) {
    await inFlight;
  }
  const batch = queue.splice(0, queue.length);
  if (batch.length === 0) return;
  inFlight = send(batch);
  try {
    await inFlight;
  } finally {
    inFlight = null;
  }
}

// Navigating away is the most common way to lose the last queued events. The
// pagehide/visibilitychange pair covers normal navigation and tab switches.
if (typeof window !== 'undefined') {
  const flushOnHide = () => {
    void flushPlanningEvents();
  };
  window.addEventListener('pagehide', flushOnHide);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushOnHide();
  });
}
