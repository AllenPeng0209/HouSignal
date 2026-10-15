export type DraftScope = { organizationId: string; userId: string; projectId: string; contentId: string };
export class ContentDraftStore<T> {
  private drafts = new Map<string, T>();
  private key(scope: DraftScope) { return JSON.stringify([scope.organizationId, scope.userId, scope.projectId, scope.contentId]); }
  set(scope: DraftScope, draft: T): void { this.drafts.set(this.key(scope), JSON.parse(JSON.stringify(draft))); }
  get(scope: DraftScope): T | undefined { const draft = this.drafts.get(this.key(scope)); return draft === undefined ? undefined : JSON.parse(JSON.stringify(draft)); }
  forget(scope: DraftScope): void { this.drafts.delete(this.key(scope)); }
  clear(): void { this.drafts.clear(); }
}
