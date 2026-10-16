import type {Page} from './types';
import {workspaceDrafts,type DraftScope} from './workspace-draft-store.js';
export interface NavigationTarget {
 contentId?:string;
 jobId?:string;
 studioStage?:'discover'|'plan'|'script'|'visual'|'review'|'publish'|'feedback';
 inquiryId?:string;
 leadId?:string;
 publicationId?:string;
 metricsWindow?:'D7'|'D30';
}
export interface WorkspaceLocation {page:Page;target:NavigationTarget}
const pages:Page[]=['assistant','dashboard','studio','accounts','skills','property','assets','leads','reports','settings'];
export function readWorkspaceLocation(scope:DraftScope):WorkspaceLocation|null{
 const saved=workspaceDrafts.load<WorkspaceLocation>(scope,'navigation','last',null)?.value;
 return saved&&pages.includes(saved.page)&&saved.target&&typeof saved.target==='object'?saved:null;
}
export function readPageTarget(scope:DraftScope,page:Page):NavigationTarget {return workspaceDrafts.load<NavigationTarget>(scope,'navigation',`page:${page}`,null)?.value||{};}
export function saveWorkspaceLocation(scope:DraftScope,page:Page,target:NavigationTarget={}){workspaceDrafts.save(scope,'navigation',`page:${page}`,null,target);return workspaceDrafts.save(scope,'navigation','last',null,{page,target});}
