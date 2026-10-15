import type { ReactNode } from 'react';
import type { Asset, Citation, Job, Project, Session } from '../api/types';

export type ScreenName = 'home' | 'projects' | 'project' | 'assets' | 'assistant' | 'studio' | 'content' | 'publications' | 'jobs' | 'leads' | 'lead' | 'reports' | 'settings' | 'accounts' | 'skills';
export type Params = { id?: string; conversationId?: string; citation?: Citation };
export type ApiOptions = { method?: string; body?: unknown; signal?: AbortSignal };
export type Api = <T = any>(path: string, options?: ApiOptions) => Promise<T>;
export type PickedFile = { uri: string; name: string; mimeType: string; size?: number; file?: File };
export interface AppState {
  api: Api;
  baseUrl: string;
  authHeaders: () => Record<string, string>;
  session: Session;
  project: Project | null;
  projects: Project[];
  refreshKey: number;
  canManage: boolean;
  canEdit: boolean;
  canEditData: boolean;
  canReview: boolean;
  canSales: boolean;
  navigate: (screen: ScreenName, params?: Params) => void;
  back: () => void;
  selectProject: (id: string) => Promise<void>;
  reload: () => Promise<void>;
  notify: (message: string) => void;
  logout: () => Promise<void>;
  trackJob: (id: string) => void;
  uploadAsset: (projectId: string, file: PickedFile, onProgress?: (fraction: number) => void) => Promise<Asset>;
  shareFile: (path: string, filename: string, mimeType?: string) => Promise<void>;
  shareText: (text: string, filename: string) => Promise<void>;
  openCitation: (citation: Citation) => void;
  notificationsEnabled: boolean;
  enableNotifications: (enabled: boolean) => Promise<void>;
  pendingJobs: Job[];
}

export type PageProps = { children: ReactNode; refreshing?: boolean; onRefresh?: () => void; scroll?: boolean; testID?: string };
