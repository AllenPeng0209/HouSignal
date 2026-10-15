export class SessionEpoch {
  token: string | null = null;
  current = 0;
  begin(token: string): number { this.current++; this.token = token; return this.current; }
  isCurrent(epoch: number): boolean { return this.current === epoch; }
  invalidate(epoch: number): boolean { if (!this.isCurrent(epoch)) return false; this.token = null; this.current++; return true; }
}
export class JobTransitions {
  track(id: string): void { if (!this.statuses.has(id)) this.statuses.set(id, 'queued'); }
  private statuses = new Map<string, string>();
  observe(jobs: {id: string; status: string}[]): string[] {
    const completed: string[] = [];
    for (const job of jobs) {
      const previous = this.statuses.get(job.id);
      if (previous && ['queued', 'running'].includes(previous) && ['succeeded', 'failed', 'cancelled'].includes(job.status)) completed.push(job.id);
      this.statuses.set(job.id, job.status);
    }
    return completed;
  }
  reset(): void { this.statuses.clear(); }
}
