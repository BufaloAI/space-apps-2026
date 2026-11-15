import Chart from 'chart.js/auto';
import {aggregate,monthly,variables,keys,firstDate,lastDate,download,demoSeries} from './data.ts';
import {$,t,lang,ic,number,demo,unit,metricHelp,esc} from './ui.js';
import {initial} from './selection.js';
const label=k=>variables[k][lang==='tr'?0:1], units=k=>unit(k)||variables[k][2];
const tones={temp:'#efae68',rain:'#6caef0',water:'#65c8b5',sun:'#e6cb63',soil:'#99b791'};
const displayDate=d=>new Date(d+'T12:00:00Z').toLocaleDateString(lang,{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'});
const monthName=m=>new Date(2024,m-1,15).toLocaleString(lang,{month:'short'});
const value=(v,k)=>v==null?'—':number(v)+' '+units(k);
const charts=new Map();
let place=initial,all=demoSeries(place),rows=all,start=firstDate,end=lastDate,source='all',variable='T2M',grain='monthly';
let page=0,sort='date',asc=true,search='',columns=new Set(['lat','lon',...keys]);
const allowed=()=>keys.filter(k=>source==='all'||(source==='smap')===(k==='SM_SURFACE'));
function scope(){return rows.filter(r=>!search||[r.date,r.lat,r.lon,...allowed().filter(k=>columns.has(k)).map(k=>r[k])].some(v=>v!=null&&String(v).toLowerCase().includes(search)));}
function scaledSeries(vals){
 const lo=Math.min(...vals),hi=Math.max(...vals);
 return vals.map((v,i)=>(i*100/Math.max(1,vals.length-1)).toFixed(1)+','+(24-21*(v-lo)/(hi-lo||1)).toFixed(1)).join(' ');
}
function spark(k){
 const vals=monthly(rows,k).map(r=>r.value).filter(v=>v!=null);if(vals.length<2)return '';
 return '<svg class="spark" viewBox="0 0 102 28" aria-hidden="true"><polyline points="'+scaledSeries(vals)+'" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"/></svg>';
}
function metric(k,compact=false){
 const v=aggregate(rows,k),tip=t(...metricHelp[k]);
 return `<article class="metric ${compact?'compact':''}" style="--tone:${tones[variables[k][4]]}" title="${esc(tip)}" tabindex="0"><div class="metric-name"><span class="metric-icon">${ic(variables[k][3])}</span><span>${label(k)}</span></div><div class="metric-value"><strong>${number(v)}</strong><small>${v==null?t('Veri yok','Unavailable'):units(k)}</small></div>${compact?'':spark(k)}</article>`;
}
function clearChart(id){charts.get(id)?.destroy();charts.delete(id);}
function plot(id,labels,series,k,bar=false){
 clearChart(id);const canvas=$(id);if(!canvas)return;
 const invalid=!series.some(s=>s.data.some(v=>v!=null));
 canvas.hidden=invalid;
 if(invalid){const empty=document.createElement('p');empty.className='no-chart';empty.textContent=t('Bu dönem için gözlem yok.','No observations in this period.');canvas.parentElement.append(empty);return;}
 const color=getComputedStyle(document.documentElement).getPropertyValue('--muted').trim(),grid=getComputedStyle(document.documentElement).getPropertyValue('--line').trim();
 const instance=new Chart(canvas,{type:bar?'bar':'line',data:{labels,datasets:series.map((s,i)=>({label:s.name,data:s.data,borderColor:s.color,backgroundColor:bar?s.color:s.color+'16',borderWidth:bar?0:2,
   fill:!bar&&series.length===1,pointRadius:labels.length===1?3:0,pointHoverRadius:4,spanGaps:false,tension:.12}))},options:{animation:false,responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
   plugins:{legend:{display:series.length>1,labels:{color,usePointStyle:true,boxWidth:7,boxHeight:7}},tooltip:{callbacks:{label:ctx=>ctx.dataset.label+' · '+value(ctx.parsed.y,k)}}},
   scales:{x:{grid:{display:false},ticks:{color,maxTicksLimit:8,maxRotation:0}},y:{beginAtZero:bar,grid:{color:grid},ticks:{color,maxTicksLimit:5},title:{display:true,text:units(k),color}}}}});
 charts.set(id,instance);
}
function updateShared(){
 for(const el of document.querySelectorAll('.place-name'))el.textContent=place.name;
 for(const el of document.querySelectorAll('.period'))el.textContent=displayDate(start)+' – '+displayDate(end);
 $('selected-coords').textContent=number(place.lat,4)+'°, '+number(place.lon,4)+'°';
 $('field-area').textContent=place.area!=null?'≈ '+number(place.area,2)+' ha':t('Nokta seçimi','Point selection');
 $('map-metrics').innerHTML=allowed().map(k=>metric(k,true)).join('');
}
export function setPlace(p){if(p.lat!==place.lat||p.lon!==place.lon){all=demoSeries(p);rows=all.filter(r=>r.date>=start&&r.date<=end);page=0;}place=p;updateShared();}
export const locationInfo=()=>place;
function filters(){
 return `<div class="filters"><label>${t('Başlangıç','Start date')}<input type="date" data-range="start" min="${firstDate}" max="${end}" value="${start}"></label><label>${t('Bitiş','End date')}<input type="date" data-range="end" min="${start}" max="${lastDate}" value="${end}"></label>
 <label>${t('Veri kaynağı','Data source')}<select data-source>${[['all',t('Tüm kaynaklar','All sources')],['power','POWER · '+t('Demo veri','Demo data')],['smap','SMAP · '+t('Veri yok','Unavailable')]].map(([v,n])=>`<option value="${v}" ${source===v?'selected':''}>${n}</option>`).join('')}</select></label></div>`;
}
function bindFilters(root,render){
 root.querySelectorAll('[data-range]').forEach(input=>input.onchange=()=>{
   if(!input.value||!input.reportValidity()){input.value=input.dataset.range==='start'?start:end;return;}
   if(input.dataset.range==='start')start=input.value;else end=input.value;
   rows=all.filter(r=>r.date>=start&&r.date<=end);page=0;updateShared();render();
 });
 root.querySelector('[data-source]').onchange=e=>{source=e.target.value;if(!allowed().includes(variable))variable=allowed()[0];page=0;updateShared();render();};
}
const title=(name,desc)=>`<div class="heading"><div><h1>${name}</h1><p class="location-line">${ic('map-pin')}${esc(place.name)} · ${desc}</p></div>${demo()}</div>`;
export function home(){
 $('home-metrics').innerHTML=['T2M','PRECTOTCORR','RH2M','ALLSKY_SFC_SW_DWN'].filter(k=>allowed().includes(k)).map(k=>metric(k)).join('')||metric('SM_SURFACE');
 const m=monthly(rows,'T2M');$('home-chart').parentElement.querySelector('.no-chart')?.remove();
 plot('home-chart',m.map(p=>p.date),[{name:label('T2M'),data:m.map(p=>source==='smap'?null:p.value),color:'#50bd94'}],'T2M');
}
export function analytics(){
 const root=$('analytics-page');clearChart('series-chart');clearChart('year-chart');
 root.innerHTML=title(t('Çevresel analitik','Environmental analytics'),t('Tarihsel zaman serileri','Historical time series'))+filters()+`
 <div class="chart-card"><div class="heading"><label>${t('Değişken','Variable')}<select id="variable">${allowed().map(k=>`<option value="${k}" ${k===variable?'selected':''}>${label(k)}</option>`).join('')}</select></label><div class="inline"><select id="granularity" aria-label="${t('Zaman adımı','Granularity')}"><option value="daily">${t('Günlük','Daily')}</option><option value="monthly">${t('Aylık','Monthly')}</option></select><button class="outline" id="png">${ic('image')} PNG</button><button class="outline" id="chart-csv">${ic('download')} CSV</button></div></div><div class="chart-space"><canvas id="series-chart"></canvas></div></div>
 <div class="chart-card"><div class="heading"><h2>${t('Yıllara göre karşılaştırma','Year comparison')}</h2><span class="micro">${label(variable)} · ${units(variable)}</span></div><div class="chart-space"><canvas id="year-chart"></canvas></div><div class="year-summary">${[2024,2025].map(y=>'<span>'+y+' <b>'+value(aggregate(rows.filter(r=>r.date.startsWith(String(y))),variable),variable)+'</b></span>').join('')}</div></div>`;
 bindFilters(root,analytics);$('granularity').value=grain;$('granularity').onchange=e=>{grain=e.target.value;analytics();};$('variable').onchange=e=>{variable=e.target.value;analytics();};
 const data=grain==='daily'?rows.map(r=>({date:r.date,value:r[variable]})):monthly(rows,variable);
 const ds=[{name:label(variable),data:data.map(p=>p.value),color:tones[variables[variable][4]]}];
 if(variable==='T2M')for(const [k,color] of [['T2M_MAX','#e07766'],['T2M_MIN','#67a8dc']])ds.push({name:label(k),data:grain==='daily'?rows.map(r=>r[k]):monthly(rows,k).map(p=>p.value),color});
 plot('series-chart',data.map(p=>p.date),ds,variable,variable==='PRECTOTCORR');
 const groups=monthly(rows,variable),months=Array.from({length:12},(_,i)=>i+1);
 plot('year-chart',months.map(monthName),[2024,2025].map((y,i)=>({name:String(y),color:i?'#6ba8dd':'#54bc93',data:months.map(m=>groups.find(g=>g.date===y+'-'+String(m).padStart(2,'0'))?.value??null)})),variable,variable==='PRECTOTCORR');
 $('png').disabled=!charts.has('series-chart');$('png').onclick=()=>{const canvas=$('series-chart'),out=document.createElement('canvas');out.width=canvas.width;out.height=canvas.height+44;const ctx=out.getContext('2d');ctx.fillStyle=getComputedStyle(root).getPropertyValue('--panel');ctx.fillRect(0,0,out.width,out.height);ctx.drawImage(canvas,0,0);ctx.fillStyle=getComputedStyle(root).color;ctx.font='16px sans-serif';ctx.fillText('BufaloAI · DEMO DATA · '+variable+' · '+start+' / '+end,12,out.height-14);const a=document.createElement('a');a.href=out.toDataURL();a.download='BufaloAI-demo-'+variable+'.png';a.click();};
 $('chart-csv').onclick=()=>download(rows,['date','lat','lon',...ds.map((s,i)=>i===0?variable:i===1?'T2M_MAX':'T2M_MIN')]);
}
export function explorer(){
 const root=$('data-page'),available=['lat','lon',...allowed()];
 const colLabel=k=>k==='date'?t('Tarih','Date'):k==='lat'?t('Enlem','Latitude'):k==='lon'?t('Boylam','Longitude'):label(k);
 root.innerHTML=title(t('Veri gezgini','Data explorer'),t('Günlük kayıtlar','Daily records'))+filters()+`<div class="chart-card"><div class="table-tools"><input id="table-search" type="search" placeholder="${t('Kayıtlarda ara…','Search records…')}" aria-label="${t('Kayıtlarda ara','Search records')}"><details class="column-menu"><summary>${ic('columns-3')}${t('Sütunlar','Columns')}</summary><div>${available.map(k=>`<label><input type="checkbox" value="${k}" ${columns.has(k)?'checked':''}> ${colLabel(k)}</label>`).join('')}</div></details><button id="table-csv" class="outline">${ic('download')}CSV</button></div><div class="table-scroll"><table><thead></thead><tbody></tbody></table></div><div class="pagination"></div></div><p class="micro">${t('Kaynak: sentetik demo. Her satır seçilen noktada bir günü temsil eder. Eksik değerler “—” ile gösterilir.','Source: synthetic demo. Each row represents one day at the selected point. Missing values appear as “—”.')}</p>`;
 bindFilters(root,explorer);$('table-search').value=search;
 function draw(){
   const cols=['date',...available.filter(k=>columns.has(k))];if(!cols.includes(sort))sort='date';
   const sorted=scope().sort((a,b)=>{let x=a[sort],y=b[sort];return (x<y?-1:x>y?1:0)*(asc?1:-1);});
   const pages=Math.max(1,Math.ceil(sorted.length/20));page=Math.min(page,pages-1);
   root.querySelector('thead').innerHTML='<tr>'+cols.map(k=>`<th aria-sort="${sort===k?(asc?'ascending':'descending'):'none'}"><button data-sort="${k}">${colLabel(k)} ${sort===k?(asc?'↑':'↓'):'↕'}</button>${variables[k]?'<small>'+k+' · '+units(k)+'</small>':''}</th>`).join('')+'</tr>';
   root.querySelector('tbody').innerHTML=sorted.slice(page*20,page*20+20).map(r=>'<tr>'+cols.map(k=>'<td>'+(k==='date'?r.date:number(r[k],k==='lat'||k==='lon'?4:1))+'</td>').join('')+'</tr>').join('')||'<tr><td colspan="'+cols.length+'">'+t('Kayıt bulunamadı.','No matching records.')+'</td></tr>';
   root.querySelector('.pagination').innerHTML=`<span>${sorted.length} ${t('kayıt','records')} · ${page+1} / ${pages}</span><div><button id="prev" aria-label="${t('Önceki','Previous')}" ${page===0?'disabled':''}>${ic('chevron-left')}</button><button id="next" aria-label="${t('Sonraki','Next')}" ${page+1===pages?'disabled':''}>${ic('chevron-right')}</button></div>`;
   $('prev').onclick=()=>{page--;draw();};$('next').onclick=()=>{page++;draw();};
   root.querySelectorAll('[data-sort]').forEach(btn=>btn.onclick=()=>{asc=sort===btn.dataset.sort?!asc:true;sort=btn.dataset.sort;page=0;draw();});
   $('table-csv').onclick=()=>download(sorted,cols);
 }
 $('table-search').oninput=e=>{search=e.target.value.trim().toLowerCase();page=0;draw();};
 root.querySelectorAll('.column-menu input').forEach(input=>input.onchange=()=>{if(input.checked)columns.add(input.value);else columns.delete(input.value);draw();});
 draw();
}
