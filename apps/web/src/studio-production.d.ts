import type {Job} from './types';
export function activeContentJob(jobs:Job[],contentId:string):Job|null;
export function resumableProductionJob(jobs:Job[],contentId:string):Job|null;
