import {api} from './api.js';
import {ConversationDraftStore} from './draft-store.js';
const report=(error:Error)=>window.dispatchEvent(new CustomEvent('housignal-draft-error',{detail:error.message}));
export const conversationDrafts=new ConversationDraftStore({
 patch:(id:string,draft:string)=>api(`/api/conversations/${id}/draft`,{method:'PATCH',body:{draft},keepalive:true}),
 send:(id:string,text:string)=>api(`/api/conversations/${id}/messages`,{method:'POST',body:{text}}),
 onError:report,
});
const flush=()=>{void conversationDrafts.flushAll().catch(report);};
window.addEventListener('blur',flush);
window.addEventListener('pagehide',flush);
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden')flush();});
