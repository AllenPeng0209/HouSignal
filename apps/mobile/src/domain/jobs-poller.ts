export type PollerOptions<T> = { read: (signal: AbortSignal) => Promise<T>; onData: (data: T) => void; onError: (error: unknown) => void; interval?: number; schedule?: (fn: () => void, delay: number) => any; unschedule?: (timer: any) => void };
export function createForegroundPoller<T>(options: PollerOptions<T>) {
  const schedule = options.schedule || ((fn, delay) => setTimeout(fn, delay));
  const unschedule = options.unschedule || (timer => clearTimeout(timer));
  let active = false, disposed = false, generation = 0;
  let timer: any = null, request: AbortController | null = null;
  async function read() {
    if (!active || disposed) return;
    timer = null;
    const revision = generation;
    const controller = new AbortController(); request = controller;
    try {
      const data = await options.read(controller.signal);
      if (active && !disposed && revision === generation) options.onData(data);
    } catch (error) {
      if (active && !disposed && revision === generation && !controller.signal.aborted) options.onError(error);
    } finally {
      if (request === controller) request = null;
      if (active && !disposed && revision === generation) timer = schedule(read, options.interval ?? 3000);
    }
  }
  function pause() {
    active = false; generation++;
    if (timer !== null) unschedule(timer);
    timer = null; request?.abort(); request = null;
  }
  return {
    resume() { if (!disposed && !active) { active = true; void read(); } },
    pause,
    dispose() { disposed = true; pause(); },
  };
}
