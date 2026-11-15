import * as icons from './icon-set.js';
export const $=id=>document.getElementById(id);
export let lang=localStorage.getItem('bufalo.lang')==='en'?'en':'tr';
export const t=(tr,en)=>lang==='tr'?tr:en;
export const number=(v,d=1)=>v==null?'—':v.toLocaleString(lang,{maximumFractionDigits:d});
export const ic=name=>icons.default[name]||'';
export const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const demo=()=>'<span class="tag demo"><b></b>'+t('Demo veri','Demo data')+'</span>';
export const unit=k=>k==='ALLSKY_SFC_SW_DWN'?t('kWh/m²/gün','kWh/m²/day'):null;
export function changeLanguage(){localStorage.setItem('bufalo.lang',lang==='tr'?'en':'tr');location.reload();}
export const metricHelp={
 T2M:['İki metre yükseklikte günlük ortalama hava sıcaklığı.','Daily mean air temperature at two metres.'],
 T2M_MAX:['Seçili dönemdeki en yüksek günlük sıcaklık.','Highest daily maximum in the selected period.'],
 T2M_MIN:['Seçili dönemdeki en düşük günlük sıcaklık.','Lowest daily minimum in the selected period.'],
 PRECTOTCORR:['Dönem içindeki günlük yağış miktarlarının toplamı.','Sum of daily precipitation over the period.'],
 RH2M:['İki metre yükseklikte ortalama bağıl nem.','Average relative humidity at two metres.'],
 ALLSKY_SFC_SW_DWN:['Yüzeye ulaşan günlük ortalama kısa dalga güneş enerjisi.','Mean daily all-sky downward shortwave solar energy.'],
 SM_SURFACE:['Seçili dönem için bir SMAP ürünü bağlı değil.','No SMAP product is connected for this period.']
};
