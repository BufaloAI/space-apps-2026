export interface Coordinates { lat: number; lon: number }
export interface FieldPolygon { type: 'Polygon'; coordinates: [number, number][][] }
export interface SessionResponse { session_id?: string; status: 'active'|'queued'|'ended'; position?: number; job_id?: string; idle_remaining?: number|null; has_analysis?: boolean }
export interface JobResponse { job_id: string }
export const firstDate = '2024-01-01', lastDate = '2025-12-31';
export const variables = {
  T2M: ['Ortalama sıcaklık','Mean temperature','°C','thermometer','temp'],
  T2M_MAX: ['En yüksek sıcaklık','Maximum temperature','°C','thermometer-sun','temp'],
  T2M_MIN: ['En düşük sıcaklık','Minimum temperature','°C','thermometer-snowflake','temp'],
  PRECTOTCORR: ['Toplam yağış','Precipitation','mm','cloud-rain','rain'],
  RH2M: ['Bağıl nem','Relative humidity','%','droplets','water'],
  ALLSKY_SFC_SW_DWN: ['Güneş ışınımı','Solar radiation','kWh/m²/gün','sun','sun'],
  SM_SURFACE: ['Yüzey toprak nemi','Surface soil moisture','m³/m³','sprout','soil'],
} as const;
export type Variable = keyof typeof variables;
export const keys = Object.keys(variables) as Variable[];
export type Observation = Coordinates & {date:string; provenance:'synthetic-demo'} & Record<Variable,number|null>;
const round=(n:number)=>Math.round(n*10)/10;
export function demoSeries(p:Coordinates):Observation[] {
  let seed = (Math.round((p.lat+90)*1000)*7919 + Math.round((p.lon+180)*1000))>>>0;
  const rand=()=>{seed=(Math.imul(1664525,seed)+1013904223)>>>0;return seed/4294967296;};
  const rows:Observation[]=[];
  for(let day=Date.parse(firstDate), i=0; day<=Date.parse(lastDate);day+=86400000,i++){
    const season=Math.cos(2*Math.PI*(i-200)/365.25)*Math.sin(p.lat*Math.PI/180);
    const temp=28-Math.abs(p.lat)*.42+18*season+2*Math.sin(i*.24)+3*(rand()-.5);
    const rain=rand()<.28?round(Math.pow(rand(),2)*24):0;
    rows.push({...p,date:new Date(day).toISOString().slice(0,10),provenance:'synthetic-demo',
      T2M:round(temp),T2M_MAX:round(temp+2+rand()*6),T2M_MIN:round(temp-2-rand()*5),
      PRECTOTCORR:rain,RH2M:round(Math.min(98,55-12*season+rain+rand()*12)),
      ALLSKY_SFC_SW_DWN:round(Math.max(.1,4+2.4*season+rand()*1.4-rain*.045)),SM_SURFACE:null});
  }
  return rows;
}
export function aggregate(rows:Observation[],key:Variable):number|null{
  const values=rows.map(r=>r[key]).filter((v):v is number=>v!=null);
  if(!values.length)return null;
  if(key==='T2M_MAX')return Math.max(...values);
  if(key==='T2M_MIN')return Math.min(...values);
  const sum=values.reduce((a,b)=>a+b,0);
  return round(key==='PRECTOTCORR'?sum:sum/values.length);
}
export function monthly(rows:Observation[],key:Variable){
  const groups=new Map<string,Observation[]>();
  for(const row of rows){const month=row.date.slice(0,7);if(!groups.has(month))groups.set(month,[]);groups.get(month)!.push(row);}
  return [...groups].map(([date,group])=>({date,value:aggregate(group,key)}));
}
export function download(rows:Observation[],columns:(keyof Observation)[]){
  const names=['provenance',...columns.filter(c=>c!=='provenance')] as (keyof Observation)[];
  const csv=[names.join(','),...rows.map(r=>names.map(c=>r[c]??'').join(','))].join('\r\n');
  const url=URL.createObjectURL(new Blob(['\uFEFF',csv],{type:'text/csv;charset=utf-8'}));
  const a=document.createElement('a');a.href=url;a.download='BufaloAI-demo-'+(rows[0]?.date||'empty')+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
