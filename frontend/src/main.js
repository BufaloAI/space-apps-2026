import '@fontsource-variable/inter';
import {shell} from './shell.js';
import {$,t,lang,ic,changeLanguage} from './ui.js';
import {home,analytics,explorer,setPlace} from './history.js';
import {request} from './api.ts';
import {initField} from './field.js';
document.documentElement.lang=lang;document.title='BufaloAI · '+t('Dünya Gözlem Platformu','Earth Intelligence');
document.documentElement.dataset.theme=localStorage.getItem('bufalo.theme')||'dark';
$('app').innerHTML=shell();
const field=initField(setPlace);
function page(){const key=location.hash.slice(1);return ['map','analytics','data','about'].includes(key)?key:'home';}
function render(){
 const current=page();document.body.dataset.page=current;
 document.querySelectorAll('.page').forEach(el=>el.hidden=el.id!==current+'-page');
 document.querySelectorAll('[data-page]').forEach(a=>{if(a.tagName!=='A')return;a.classList.toggle('active',a.dataset.page===current);if(a.dataset.page===current)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
 if(current==='home')home();if(current==='map')field.visible();if(current==='analytics')analytics();if(current==='data')explorer();
 $('theme').innerHTML=ic(document.documentElement.dataset.theme==='dark'?'sun':'moon');
}
window.addEventListener('hashchange',()=>{window.scrollTo(0,0);render();});render();
$('theme').onclick=()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=theme;localStorage.setItem('bufalo.theme',theme);render();};
$('language').onclick=changeLanguage;
function panel(open){$('map-panel').hidden=!open;$('panel-open').hidden=open;$('panel-open').setAttribute('aria-expanded',open);}
$('panel-close').onclick=()=>panel(false);$('panel-open').onclick=()=>panel(true);
if(matchMedia('(max-width:720px)').matches)panel(false);
async function health(){
 $('connection').disabled=true;$('health').textContent=t('Bağlanıyor','Connecting');
 try{const s=await request('/health');if(!s.ok)throw Error();$('health').textContent=t('API bağlı','API online');$('connection').dataset.ok='true';}
 catch{$('health').textContent=t('API çevrimdışı','API offline');$('connection').dataset.ok='false';}
 finally{$('connection').disabled=false;$('connection').title=t('API bağlantısını yeniden denetle','Check API connection');}
}
$('connection').onclick=health;void health();
const places=[['Çumra, Konya',37.6059,32.7506],['Nairobi, Kenya',-1.286,36.817],['Sacramento, California',38.582,-121.494],['São Paulo, Brasil',-23.55,-46.633],['Lyon, France',45.764,4.836],['Punjab, India',30.9,75.85],['Australia',-25.274,133.775],['Türkiye',39,35],['Japan',36.2,138.25]];
const normalize=s=>s.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'').replaceAll('ı','i');
let searchNo=0;
function matches(list){
 $('search-results').replaceChildren();
 for(const [name,lat,lon] of list){
  const button=document.createElement('button');button.className='search-match';const label=document.createElement('strong');label.textContent=name;
  const small=document.createElement('small');small.textContent=lat.toFixed(3)+'°, '+lon.toFixed(3)+'°';button.append(label,small);
  button.onclick=()=>{if(!field.select({name,lat,lon},11)){message(t('Önce çalışan analizin bitmesini bekleyin.','Wait for the current analysis to finish.'));return;}
    $('search-dialog').close();location.hash='map';render();field.select({name,lat,lon},11);panel(true);};
  $('search-results').append(button);
 }
}
function message(s){$('search-results').textContent=s;}
const dialog=$('search-dialog');
function openSearch(){dialog.showModal();$('query').value='';matches(places.slice(0,5));$('query').focus();}
$('map-search').onclick=openSearch;$('search-open').onclick=openSearch;$('search-close').onclick=()=>dialog.close();
dialog.addEventListener('close',()=>{searchNo++;});
$('query').oninput=()=>{searchNo++;const q=normalize($('query').value.trim());matches(places.filter(p=>normalize(p[0]).includes(q)).slice(0,5));};
$('location-form').onsubmit=async e=>{
 e.preventDefault();const q=$('query').value.trim(),id=++searchNo;if(!q){matches(places.slice(0,5));return;}
 if(/^[+\-\d.,;\s]+$/.test(q)){
  const nums=q.split(/\s*[,;]\s*|\s+/).map(Number);
  if(nums.length!==2||!nums.every(Number.isFinite)||Math.abs(nums[0])>90||Math.abs(nums[1])>180){message(t('Enlem −90…90, boylam −180…180 aralığında olmalı.','Latitude must be −90…90 and longitude −180…180.'));return;}
  matches([[q,...nums]]);return;
 }
 const local=places.filter(p=>normalize(p[0]).includes(normalize(q)));if(local.length){matches(local);return;}
 message(t('Konumlar aranıyor…','Searching locations…'));
 try{const u=new URL('https://nominatim.openstreetmap.org/search');u.search=new URLSearchParams({q,format:'jsonv2',limit:'5','accept-language':lang});
  const res=await fetch(u,{signal:AbortSignal.timeout(8000)});if(!res.ok)throw Error();
  const items=await res.json();if(id!==searchNo)return;
  if(items.length){matches(items.map(x=>[x.display_name,Number(x.lat),Number(x.lon)]));const credit=document.createElement('small');credit.textContent='© OpenStreetMap contributors · Nominatim';$('search-results').append(credit);}
  else message(t('Konum bulunamadı. Koordinat girebilirsiniz.','No location found. Try coordinates.'));
 }catch{if(id===searchNo)message(t('Arama servisine ulaşılamadı. Koordinat girebilirsiniz.','Location service is unavailable. Enter coordinates instead.'));}
};
