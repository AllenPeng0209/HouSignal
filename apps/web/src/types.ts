export type Page='assistant'|'dashboard'|'studio'|'accounts'|'skills'|'property'|'assets'|'leads'|'reports'|'settings';
export interface Session { user:{id:string;name:string;email:string}; organization:{id:string;name:string};role:string }
export interface Readiness {percent:number;completed:number;total:number;items:{key:string;label:string;complete:boolean;detail:string;target:string}[]}
export interface Project {id:string;name:string;city:string;property_type:string;owner_name:string;audience:string;sales_owner_name:string;channel:string;marketing_confirmed:boolean;marketing_evidence:string;is_test:boolean;completeness:Readiness;created_at?:string}
export interface Fact {id:string;label:string;semantic_key:string;value:string;unit:string;source:string;status:string;revision:number;required:boolean;valid_until?:string|null;updated_at:string}
export interface Asset {id:string;name:string;kind:string;mime_type:string;byte_size:number;rights:string;rights_evidence:string;origin_type:string;metadata_json?:Record<string,unknown>;extracted_text?:string;model_processing_authorized?:boolean;created_at:string}
export interface Topic {id:string;title:string;angle:string;eligible:boolean;missing:string[];fact_refs:unknown[]}
export interface StoryboardChapter {id:string;title:string;purpose?:string;plan_sections:string[];reference_beat_ids:string[];target_seconds:number;shot_ids:string[];[key:string]:unknown}
export interface StoryboardShot {id:string;chapter_id:string;plan_section:string;reference_beat_ids:string[];voiceover:string;visual_direction:string;visual_type:string;caption:string;fact_refs:unknown[];asset_id:string|null;production_status:'ready'|'planned';duration:number;start:number;[key:string]:unknown}
export interface LongformStoryboard {schema_version:2;target_seconds:number;chapters:StoryboardChapter[];shots:StoryboardShot[];planning_context?:Record<string,unknown>;voiceover?:Record<string,unknown>;[key:string]:unknown}
export interface Content {id:string;series_id:string;version:number;topic:string;script:string;status:string;channel:string;cover_title:string;aspect_ratio:string;subtitle_style:string;storyboard:unknown[]|LongformStoryboard|Record<string,unknown>;fact_refs:unknown[];render_asset_id?:string|null;created_at:string;release_id?:string;plan_id?:string|null;planning?:Record<string,unknown>;source?:Record<string,unknown>}
export interface Release {id:string;status:string;content_version_id:string;snapshot:unknown;created_at:string}
export interface Publication {id:string;release_id:string;title:string;channel:string;url:string;platform_state:string;receipt_status:string;metrics:Record<string,Record<string,number|null>|null>;created_at:string}
export interface Inquiry {id:string;name:string;need:string;channel:string;source_confidence:string;source_text:string;contact_masked?:string;contact?:string;contactable:boolean;consent:boolean;qualified:boolean;owner_id?:string|null;created_at:string}
export interface Lead {id:string;inquiry_id:string;name:string;need:string;stage:string;owner_id?:string;owner_name?:string;next_action:string;source_confidence:string;created_at:string;updated_at:string;stage_history?:unknown[]}
export interface FollowUp {id:string;note:string;next_action:string;created_at:string;created_by:string}
export interface LeadProfile {background:string;financial_situation:string;budget:string;preferences:string;family:string;requirements:string;notes:string;source_note?:string;provenance?:Record<string,unknown>;updated_at?:string|null}
export interface Viewing {id:string;lead_id:string;lead_name?:string;date:string|null;time_window:string;starts_at:string|null;ends_at?:string|null;timezone:string;location:string;notes:string;status:'pending_details'|'scheduled'|'completed'|'cancelled';missing_details?:string[];clarification?:string;created_at?:string;updated_at?:string}
export interface LeadDetail {lead:Lead;inquiry:Inquiry;followups:FollowUp[];profile?:LeadProfile;viewings?:Viewing[]}
export interface Proposal {id:string;title:string;reason:string;status:string;source_refs:unknown[];created_at:string}
export interface Metrics {published:number;inquiries:number;qualified:number;appointments:number;visits:number;views:number|null;unknown_rate:number|null}
export interface Overview {project:Project;metrics:Metrics;pending_inquiries:Inquiry[];content_versions:Content[];publications:Publication[];proposals:Proposal[]}
export interface Report {metrics:{views:number|null;likes:number|null;comments:number|null;saves:number|null;inquiries:number|null;qualified:number|null;window_complete:boolean;observed_publications:number;total_publications:number};publications:Publication[];proposals:Proposal[];window:string}
export interface Citation {kind:string;id?:string;label:string;page?:string;revision?:number}
export interface SecretaryAction {id?:string;name?:string;label:string;status:'applied'|'queued';summary:string;sources?:Citation[];changed_fields?:string[]}
export interface ChatMessage {id:string;role:string;content:string;citations:Citation[];actions?:SecretaryAction[];status:string;job_id?:string;created_at:string}
export interface Conversation {id:string;title:string;draft:string;created_at:string}
export type ProductionRequest={kind:'generate_content';topic_id:string;plan_id?:string|null}|{kind:'render_content'|'illustrate_content';content_version_id:string};
export interface Job {id:string;kind:string;platform?:string;can_verify?:boolean;skill_id?:string|null;status:string;progress:number;waiting_for_job_id?:string;phase?:string;error?:string;production_request?:ProductionRequest|null;result?:Record<string,unknown>;created_at:string;updated_at:string;started_at?:string|null;attempt_started_at?:string|null;completed_at?:string|null;first_token_at?:string|null}
export type AgentControlKind='steer'|'follow_up';
export interface AgentControlResult {control_id:string;job_id:string;user_message_id:string}
export interface ModelStatus {provider:string;model:string;configured:boolean;agent_engine?:{name:string;version:string;available:boolean}}
