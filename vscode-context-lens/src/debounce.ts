/**
 * debounce.ts — debounce helpers with leading-edge support.
 *
 * `leadingDebounce`: fires immediately on the first call, then coalesces all
 * subsequent calls within `ms` into a single trailing call. Ideal for file
 * watchers where the first event should feel instant but bursts should be
 * coalesced.
 */

export interface Debounced {
  (): void;
  cancel: () => void;
  flush: () => void;
}

export function leadingDebounce(fn: () => void, ms: number): Debounced {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let lastCall = 0;
  let pending = false;

  const invoke = (): Debounced => {
    const now = Date.now();
    if (now - lastCall >= ms) {
      // Leading edge — fire immediately
      lastCall = now;
      pending = false;
      fn();
    } else {
      // Coalesce trailing call
      pending = true;
      if (timer) { return invoke as unknown as Debounced; }
      const remaining = ms - (now - lastCall);
      timer = setTimeout(() => {
        timer = null;
        if (pending) {
          pending = false;
          lastCall = Date.now();
          fn();
        }
      }, remaining);
    }
    return invoke as unknown as Debounced;
  };

  const debounced = invoke as unknown as Debounced;
  debounced.cancel = (): void => {
    if (timer) { clearTimeout(timer); timer = null; }
    pending = false;
  };
  debounced.flush = (): void => {
    if (timer) { clearTimeout(timer); timer = null; }
    if (pending) {
      pending = false;
      lastCall = Date.now();
      fn();
    }
  };
  return debounced;
}
