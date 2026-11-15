import L from 'leaflet';
import overview from './assets/cumra.jpg';
import { $,t,ic } from './src/ui.js';
import {geometry,initial} from './src/selection.js';
const unproject=(x,y)=>L.CRS.EPSG3857.unproject(L.point(x,y));
const west=3643690.6845,south=4522482.3483,w=3600,h=2304;
const bounds=L.latLngBounds(unproject(west,south),unproject(west+w,south+h));
const sample=[[.482,.218],[.581,.198],[.665,.139],[.679,.554],[.519,.624],[.494,.618]].map(([x,y])=>unproject(west+x*w,south+(1-y)*h));
export function createParcelMap(onReady,activity,onLocation){
  const map=L.map('map',{zoomControl:false,scrollWheelZoom:true,minZoom:2,maxZoom:19,worldCopyJump:true,zoomSnap:.25});
  const tile=(url,attribution,maxNativeZoom=19)=>L.tileLayer(url,{attribution,maxNativeZoom,maxZoom:19});
  const sat=tile('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}','Esri, Maxar, Earthstar Geographics and contributors');
  const cache=L.imageOverlay(overview,bounds,{pane:'tilePane',zIndex:2});
  const basemaps={sat:L.layerGroup([sat,cache]),street:tile('https://tile.openstreetmap.org/{z}/{x}/{y}.png','© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'),
    terrain:tile('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}','Esri, USGS and contributors',16)};
  let basemap=basemaps.sat.addTo(map), points=sample.slice(),mode='view',saved,example=true,locked=false,selected={...initial},initialized=false;
  const shapes=L.layerGroup().addTo(map);
  const pin=L.marker([selected.lat,selected.lon],{interactive:false,keyboard:false,icon:L.divIcon({className:'selected-pin',html:'<b></b>',iconSize:[20,20],iconAnchor:[10,10]})}).addTo(map);
  map.attributionControl.setPrefix(false);L.control.scale({imperial:false}).addTo(map);
  function hint(s=''){$('map-hint').hidden=!s;$('map-hint').textContent=s;}
  for(const layer of [sat,basemaps.street,basemaps.terrain])layer.on('tileerror',()=>{if(map.hasLayer(layer)&&!(layer===sat&&bounds.contains(map.getBounds())))hint(t('Harita servisine ulaşılamadı. Altlığı değiştirebilirsiniz.','Tile service unavailable. Try a different basemap.'));});
  $('basemap').onchange=e=>{map.removeLayer(basemap);basemap=basemaps[e.target.value].addTo(map);hint();};
  function fit(){
    map.invalidateSize();
    const pad=map.getSize().x>800&& !$('map-panel').hidden?[385,65]:[32,65];
    if(points.length>1)map.fitBounds(L.latLngBounds(points),{paddingTopLeft:[40,65],paddingBottomRight:pad,maxZoom:17,animate:false});
    else map.setView([selected.lat,selected.lon],12);
  }
  function update(){
    const g=geometry(points),ready=!!g&&mode==='view';
    if(ready)selected={lat:g.lat,lon:g.lon,name:example?initial.name:t('Seçili parsel','Selected field')};
    pin.setLatLng([selected.lat,selected.lon]);onLocation({...selected,area:ready?g.area:null});
    $('draw').disabled=locked||mode==='edit'||(mode==='draw'&&points.length<3);
    $('edit').disabled=locked||mode==='draw'||points.length<3;
    $('clear').disabled=locked||(!points.length&&mode==='view');
    for(const [id,active] of [['draw',mode==='draw'],['edit',mode==='edit']])$(id).setAttribute('aria-pressed',active);
    $('draw').title=$('draw').ariaLabel=mode==='draw'?t('Çizimi bitir','Finish drawing'):t('Parsel çiz','Draw field');
    $('edit').title=$('edit').ariaLabel=mode==='edit'?t('Düzenlemeyi bitir','Finish editing'):t('Köşeleri düzenle','Edit vertices');
    $('map').classList.toggle('drawing',mode==='draw');onReady(ready);
  }
  function render(){
    shapes.clearLayers();const poly=L.polygon(points,{color:'#b6ffda',fillColor:'#29b57f',weight:2.5,fillOpacity:.24,interactive:false}).addTo(shapes);
    points.forEach((p,i)=>{
      const editing=mode==='edit';
      const marker=L.marker(p,{draggable:editing,interactive:editing||(i===0&&mode==='draw'),keyboard:editing,title:t('Köşe ','Vertex ')+(i+1),
        icon:L.divIcon({className:'vertex',iconSize:[12,12]})}).addTo(shapes);
      marker.on('drag',()=>{points[i]=marker.getLatLng().wrap();example=false;poly.setLatLngs(points);});
      marker.on('dragend',()=>{update();activity();});
      marker.on('click',()=>{if(mode==='draw'&&points.length>=3)finish();else if(editing&&points.length>3){
        const del=document.createElement('button');del.textContent=t('Bu köşeyi sil','Remove this vertex');del.onclick=()=>{points.splice(i,1);example=false;map.closePopup();render();};L.popup().setLatLng(p).setContent(del).openOn(map);
      }});
    });update();
  }
  function finish(){mode='view';saved=null;hint();render();activity();}
  $('draw').onclick=()=>{if(mode==='draw')return finish();saved={points:points.slice(),example};points=[];example=false;mode='draw';hint(t('En az 3 köşe seçin. Bitirmek için çizim düğmesine, vazgeçmek için Esc’ye basın.','Select at least 3 vertices. Press draw to finish or Esc to cancel.'));render();};
  $('edit').onclick=()=>{if(mode==='edit')return finish();mode='edit';hint(t('Köşeleri taşıyın veya silmek için dokunun. Bitirmek için kaleme basın.','Drag vertices or tap one to remove it. Press the pencil to finish.'));render();};
  $('clear').onclick=()=>{points=[];example=false;finish();};
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&mode==='draw'&&saved){points=saved.points;example=saved.example;finish();}});
  function select(p,zoom){
    if(locked)return false;
    points=[];example=false;selected={...p,name:p.name||t('Seçili nokta','Selected point')};finish();
    if(zoom)map.setView([p.lat,p.lon],zoom,{animate:false});return true;
  }
  map.on('click',e=>{if(locked)return;const p=e.latlng.wrap();
    if(mode==='draw'&&points.length<1000){points.push(p);render();activity();}
    else if(mode==='view')select({lat:p.lat,lon:p.lng});
  });
  $('home').onclick=fit;$('zoom-in').onclick=()=>map.zoomIn();$('zoom-out').onclick=()=>map.zoomOut();
  map.on('dragend zoomend',activity);map.setView([initial.lat,initial.lon],16);render();
  new ResizeObserver(()=>{if($('map').clientWidth){map.invalidateSize();if(!initialized){fit();initialized=true;}}}).observe($('map'));
  return {fit,select,visible(){map.invalidateSize();if(!initialized){fit();initialized=true;}},polygon(){const ring=points.map(p=>[p.lng,p.lat]);return{type:'Polygon',coordinates:[[...ring,ring[0]]]};},
    lock(v){locked=v;update();},isExample(){return example;},restore(r,ex){if(!r?.length)return;points=r.map(([x,y])=>L.latLng(y,x));example=ex;finish();if($('map').clientWidth)fit();}};
}
