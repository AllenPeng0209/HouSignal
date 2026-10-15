export function stageAgentPrompt(storage: Pick<Storage,'setItem'>, projectId:string, prompt:string):boolean;
export function consumeAgentPrompt(storage: Pick<Storage,'getItem'|'removeItem'>, projectId:string, existingDraft?:string):string;
