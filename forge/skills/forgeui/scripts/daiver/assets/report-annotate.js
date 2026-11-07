(function(){
var D=JSON.parse(document.getElementById('report-data').textContent),dec={},marks={},draft=null,mode=false,hit=null,drag=null;
Object.keys(D.decisionsFile).forEach(function(k){dec[k]=D.decisionsFile[k]});
Object.keys(D.marksFile).forEach(function(k){marks[k]=D.marksFile[k]});
function $(id){return document.getElementById(id)}
function el(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e}
function who(){return $('reviewer').value.trim()}
function stamp(o){var w=who();if(w)o.reviewer=w;o.decidedAt=new Date().toISOString();return o}
function exportObj(){
  var ds=Object.keys(dec).map(function(k){return dec[k]}).concat(D.foreign.decisions);
  var ms=Object.keys(marks).map(function(k){return marks[k]}).concat(D.foreign.marks);
  return {schema:1,decisions:ds,marks:ms}}
window.__daiverExport=exportObj;
function refresh(){var o=exportObj();$('export-json').value=JSON.stringify(o,null,2)+'\n';
  $('export-count').textContent='решений: '+Object.keys(dec).length+' · меток: '+Object.keys(marks).length}
function sync(card){var id=card.dataset.id,d=dec[id];
  card.querySelectorAll('[data-verdict]').forEach(function(b){b.classList.toggle('on',!!d&&d.verdict===b.dataset.verdict)});
  card.querySelector('.dnote').value=d&&d.note?d.note:''}
function decide(card,verdict){var id=card.dataset.id,note=card.querySelector('.dnote').value.trim(),o={id:id,verdict:verdict,frame:D.frame,snapshot:D.snapshots[id]||{rows:[]}};
  if(note)o.note=note;dec[id]=stamp(o);sync(card);refresh()}
document.querySelectorAll('.defect').forEach(function(card){
  var box=el('div','decide');
  [['fix','чинить'],['skip','пропустить'],['false_positive','ложное срабатывание']].forEach(function(v){
    var b=el('button','vb',v[1]);b.type='button';b.dataset.verdict=v[0];b.addEventListener('click',function(){decide(card,v[0])});box.appendChild(b)});
  var n=el('input','dnote');n.type='text';n.placeholder='заметка';n.addEventListener('input',function(){var d=dec[card.dataset.id];if(d){if(n.value.trim())d.note=n.value.trim();else delete d.note;stamp(d);refresh()}});box.appendChild(n);
  var r=el('button','vb','снять');r.type='button';r.addEventListener('click',function(){delete dec[card.dataset.id];sync(card);refresh()});box.appendChild(r);
  if(marks[card.dataset.id]){var x=el('button','vb','удалить метку');x.type='button';x.addEventListener('click',function(){removeMark(card.dataset.id,card)});box.appendChild(x)}
  card.appendChild(box);sync(card)});
function removeMark(id,card){delete marks[id];delete dec[id];
  document.querySelectorAll('[data-mid="'+id+'"],.pin[data-n="'+(card?card.dataset.n:'-')+'"]').forEach(function(e){e.hidden=true});
  if(card){card.classList.add('removed');var t=card.querySelector('.removed-note');if(!t){t=el('div','note removed-note','метка будет удалена из файла при экспорте');card.appendChild(t)}sync(card)}refresh()}
// ── «Пометить проблему»
function pane(side){return document.querySelector('.shot[data-side="'+side+'"]')}
function size(side){return D.images[side]}
function place(side,box){var s=size(side),o=side==='impl'?D.clip:[D.rootSize.x,D.rootSize.y],w=side==='impl'?s.w:D.rootSize.w,h=side==='impl'?s.h:D.rootSize.h;
  return 'left:'+(box.x-o[0])/w*100+'%;top:'+(box.y-o[1])/h*100+'%;width:'+box.w/w*100+'%;height:'+box.h/h*100+'%'}
function toBox(side,f0,f1){var s=size(side),x0=Math.min(f0.x,f1.x),y0=Math.min(f0.y,f1.y),x1=Math.max(f0.x,f1.x),y1=Math.max(f0.y,f1.y),b;
  if(side==='impl')b={x:x0*s.w+D.clip[0],y:y0*s.h+D.clip[1],w:(x1-x0)*s.w,h:(y1-y0)*s.h};
  else b={x:D.rootSize.x+x0*D.rootSize.w,y:D.rootSize.y+y0*D.rootSize.h,w:(x1-x0)*D.rootSize.w,h:(y1-y0)*D.rootSize.h};
  return {x:Math.round(b.x),y:Math.round(b.y),w:Math.max(1,Math.round(b.w)),h:Math.max(1,Math.round(b.h))}}
function status(t){$('mark-status').textContent=t}
function showDraft(side,box,nodeId){clearDraft();draft={side:side,box:box,nodeId:nodeId||null};
  var d=el('div','mark-draft');d.setAttribute('style',place(side,box));pane(side).appendChild(d);draft.el=d;
  $('mark-form').hidden=false;$('mark-what').textContent=(side==='impl'?'реализация':'макет')+': '+(nodeId?nodeId+' · ':'')+box.w+'×'+box.h+' в ('+box.x+', '+box.y+')';$('mark-note').focus()}
function clearDraft(){if(draft&&draft.el)draft.el.remove();draft=null;$('mark-form').hidden=true;$('mark-note').value=''}
function nextId(){var id;do{id=D.markPrefix+(D.nextMark++)}while(marks[id]||D.defects.indexOf(id)>=0||dec[id]);return id}
function drawMark(id,m){var s=pane(m.side);if(!s)return;var p=el('div','pin user');p.dataset.mid=id;p.setAttribute('style',place(m.side,m.box));p.appendChild(el('b',null,'✎'));s.appendChild(p)}
$('mark-add').addEventListener('click',function(){if(!draft)return;var note=$('mark-note').value.trim();if(!note){status('Заметка обязательна: метка без постановки не принимается.');return}
  var id=nextId(),m={id:id,note:note,side:draft.side,box:draft.box,frame:D.frame,iteration:D.iteration};if(draft.nodeId)m.nodeId=draft.nodeId;marks[id]=stamp(m);
  drawMark(id,m);var c=el('div','defect user-mark');c.dataset.id=id;c.dataset.mid=id;c.appendChild(el('b',null,'✎ '+id));c.appendChild(el('div',null,note));
  var x=el('button','vb','удалить');x.type='button';x.addEventListener('click',function(){removeMark(id,null);c.remove()});c.appendChild(x);$('my-marks').appendChild(c);
  clearDraft();status('Метка '+id+' добавлена; экспортируй файл внизу страницы.');refresh()});
$('mark-cancel').addEventListener('click',function(){clearDraft();status('')});
$('mark-toggle').addEventListener('click',function(){mode=!mode;document.body.classList.toggle('marking',mode);this.classList.toggle('on',mode);
  status(mode?'Кликни по элементу на реализации (повторный клик по тому же месту — шаг наружу) или протяни рамку на любой картинке.':'')});
function candidates(f){var s=size('impl'),px=f.x*s.w+D.clip[0],py=f.y*s.h+D.clip[1],out=[];
  D.boxes.forEach(function(b,i){if(px>=b[1]&&px<=b[1]+b[3]&&py>=b[2]&&py<=b[2]+b[4])out.push({i:i,id:b[0],box:{x:b[1],y:b[2],w:b[3],h:b[4]},area:b[3]*b[4]})});
  out.sort(function(a,b){return a.area-b.area||b.i-a.i});return out}
function clickHit(side,f){
  if(side!=='impl'){status('На макете элементов для выбора нет: протяни рамку.');return}
  var s=size('impl');
  if(hit&&Math.abs(hit.f.x-f.x)*s.w<4&&Math.abs(hit.f.y-f.y)*s.h<4&&hit.list.length)hit.k=(hit.k+1)%hit.list.length;
  else{hit={f:f,list:candidates(f),k:0}}
  if(!hit.list.length){status('Под курсором нет элементов страницы: протяни рамку.');return}
  var c=hit.list[hit.k];showDraft('impl',{x:Math.round(c.box.x),y:Math.round(c.box.y),w:Math.max(1,Math.round(c.box.w)),h:Math.max(1,Math.round(c.box.h))},c.id);
  status('Выбран элемент '+(hit.k+1)+' из '+hit.list.length+'; клик в том же месте — шаг наружу.')}
function finishRect(side,f0,f1){
  if(side==='design'&&!(D.rootSize&&D.rootSize.w&&D.rootSize.h)){status('У корня кадра нет числового размера: метку на макете поставить нельзя, пометь на реализации.');return}
  var b=toBox(side,f0,f1);
  if(b.x<0||b.y<0){status('Рамка уходит в отрицательные координаты макета — такую метку файл не принимает: пометь на реализации.');return}
  showDraft(side,b,null);status('')}
document.querySelectorAll('.shot[data-side]').forEach(function(shot){
  var side=shot.dataset.side,img=shot.querySelector('img');
  img.addEventListener('dragstart',function(e){e.preventDefault()});
  function pt(ev){var r=img.getBoundingClientRect();return {x:Math.min(1,Math.max(0,(ev.clientX-r.left)/r.width)),y:Math.min(1,Math.max(0,(ev.clientY-r.top)/r.height)),cx:ev.clientX,cy:ev.clientY}}
  shot.addEventListener('pointerdown',function(ev){if(!mode||ev.button!==0)return;ev.preventDefault();drag={side:side,from:pt(ev),el:null};try{shot.setPointerCapture(ev.pointerId)}catch(e){}});
  shot.addEventListener('pointermove',function(ev){if(!drag||drag.side!==side)return;var p=pt(ev);if(!drag.el&&Math.abs(p.cx-drag.from.cx)<4&&Math.abs(p.cy-drag.from.cy)<4)return;
    if(!drag.el){drag.el=el('div','mark-draft');shot.appendChild(drag.el)}
    drag.el.setAttribute('style','left:'+Math.min(p.x,drag.from.x)*100+'%;top:'+Math.min(p.y,drag.from.y)*100+'%;width:'+Math.abs(p.x-drag.from.x)*100+'%;height:'+Math.abs(p.y-drag.from.y)*100+'%')});
  shot.addEventListener('pointerup',function(ev){if(!drag||drag.side!==side)return;var d=drag,p=pt(ev);drag=null;try{shot.releasePointerCapture(ev.pointerId)}catch(e){}
    if(d.el){d.el.remove();finishRect(side,d.from,p)}else clickHit(side,p)})});
$('export-dl').addEventListener('click',function(){var blob=new Blob([$('export-json').value],{type:'application/json'}),a=document.createElement('a');
  a.href=URL.createObjectURL(blob);a.download='verify-decisions.json';document.body.appendChild(a);a.click();a.remove()});
refresh()})();
