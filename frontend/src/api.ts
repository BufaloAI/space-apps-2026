import type {SessionResponse,JobResponse} from './data';
export const api=(import.meta.env.VITE_API_BASE_URL||'/api').replace(/\/$/,'');
export function request(path:'/session', method:'POST'):Promise<SessionResponse>;
export function request(path:'/analysis'|'/ask',method:'POST',body:object):Promise<JobResponse>;
export function request<T=unknown>(path:string,method?:string,body?:object):Promise<T>;
export async function request(path:string,method='GET',body?:object){
  const res=await fetch(api+path,{method,cache:'no-store',signal:AbortSignal.timeout(12000),
    ...(body?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
  if(!res.ok){
    const d=(await res.json().catch(()=>({}))).detail;
    const e=Object.assign(new Error(typeof d==='string'?d:d?.status||('HTTP '+res.status)),{status:res.status,detail:d});
    throw e;
  }
  return res.json();
}
