import type {ChatMessage,SecretaryAction,LeadProfile,Viewing} from './types';
export function profileChanges(previous:Partial<LeadProfile>|null|undefined,draft:Partial<LeadProfile>):Partial<LeadProfile>;
export function appliedActions(message:Pick<ChatMessage,'actions'>):SecretaryAction[];
export function operationActions(message:Pick<ChatMessage,'actions'>):SecretaryAction[];
export function viewingWhen(viewing:Partial<Viewing>):string;
export function presentAssistantAnswer(content:string):string;

export function assistantInlineParts(text:string):({kind:'text';text:string}|{kind:'asset';url:string;label:string})[];
