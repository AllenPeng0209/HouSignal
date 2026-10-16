export interface DraftScope {userId:string;organizationId:string;projectId:string}
export interface DraftAlternative<T> {id:string;writerId:string;value:T;savedAt:number}
export interface DraftRecord<T> {id:string;writerId:string;isOwn:boolean;value:T;conflict:boolean;conflictKind:'server'|'concurrent'|null;alternatives:DraftAlternative<T>[];acknowledged:string[];persisted:boolean;savedAt:number}
export function draftFingerprint(value:unknown):string;
export class WorkspaceDraftStore {
 readonly writerId:string;
 constructor(storage?:()=>Storage,options?:{writerId?:string;sessionStorage?:()=>Storage|null});
 save<T>(scope:DraftScope,kind:string,id:string,base:unknown,value:T,options?:{acknowledged?:string[]}):{ok:boolean;savedAt?:number;error?:string};
 load<T>(scope:DraftScope,kind:string,id:string,base:unknown):DraftRecord<T>|null;
 remove(scope:DraftScope,kind:string,id:string):void;
 clearIdentity(scope:Pick<DraftScope,'userId'|'organizationId'>):void;
 subscribe(scope:DraftScope,kind:string,id:string,listener:()=>void):()=>void;
 hasUnpersisted():boolean;
}
export const workspaceDrafts:WorkspaceDraftStore;
