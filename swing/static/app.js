"use strict";
const $ = (id) => document.getElementById(id);
const names = {pullback:"Repli de tendance", breakout:"Cassure de consolidation", mean_reversion:"Retour à la moyenne"};
const statuses = {candidate:"Signal à examiner", abstention:"Abstention", insufficient:"Historique insuffisant", error:"Erreur de calcul"};
const kinds = {import:"Import", settings:"Hypothèses", decision:"Décision", entry:"Entrée paper", exit:"Sortie paper", session:"Séance traitée", risk_rejection:"Refus du risque", error:"Erreur", worker_error:"Erreur moteur"};
const usd = (n) => new Intl.NumberFormat("fr-CA", {style:"currency", currency:"USD", maximumFractionDigits:2}).format(Number(n));
const pct = (n) => new Intl.NumberFormat("fr-FR", {maximumFractionDigits:2}).format(Number(n)) + " %";
let state = null, busy = false, settingsRendered = false, signature = "";
function node(tag, text, cls) { const el = document.createElement(tag); if(text !== undefined) el.textContent = text; if(cls) el.className=cls; return el; }
function message(text, error=false) { $("message").textContent=text; $("message").className=error?"error":""; }
async function api(path, payload) {
  const options = payload === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json", "X-Swing-Local":"1"}, body:JSON.stringify(payload)};
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Requête refusée");
  return data;
}
async function action(path, payload={}, success="Action terminée.") {
  if(busy) return;
  busy=true; controls();
  try { const result=await api(path,payload); message(result.status==="finished"?"Fin des données disponibles. Le moteur attend un nouvel import.":success); await refresh(); }
  catch(error) { message(error.message,true); }
  finally { busy=false; controls(); }
}
function controls() {
  const loaded=Boolean(state?.data.count), running=Boolean(state?.worker.running);
  $("fixture").disabled=busy||Boolean(state?.dataset);
  $("step").disabled=busy||!loaded||running;
  $("start").disabled=busy||!loaded||running;
  $("stop").disabled=busy||!running;
  $("import-file").disabled=busy||running;
  $("save-settings").disabled=busy||Boolean(state?.cursor)||running;
}
function table(container, headers, rows, empty) {
  const target=$(container); target.replaceChildren();
  if(!rows.length) {target.append(node("p",empty,"empty"));return;}
  const scroll=node("div",undefined,"table-scroll"), t=node("table"), head=node("thead"), hr=node("tr"), body=node("tbody");
  headers.forEach(h=>{const th=node("th",h);th.scope="col";hr.append(th);}); head.append(hr);
  rows.forEach(row=>{const tr=node("tr");row.forEach(cell=>{const td=node("td");if(cell instanceof Node){td.append(cell);td.className="wrap";}else td.textContent=String(cell);tr.append(td);});body.append(tr);});
  t.append(head,body);scroll.append(t);target.append(scroll);
}
function renderDecisions() {
  const target=$("decisions");target.replaceChildren();
  if(!state.decisions.length){target.append(node("p","Traitez une séance. Les stratégies demandent 25 séances complètes d’historique.","empty"));return;}
  state.decisions.forEach(d=>{
    const card=node("article",undefined,"decision-card "+d.status);
    const row=node("div",undefined,"title-line");row.append(node("span",d.symbol,"symbol"),node("span",statuses[d.status],"tag"));
    card.append(row,node("h3",names[d.strategy]),node("p",d.reason));
    if(d.stop) card.append(node("p","Stop "+usd(d.stop)+" · Objectif "+usd(d.target),"levels"));
    card.append(node("p","Clôture "+d.signal_date+" · "+usd(d.reference),"hint"));target.append(card);
  });
}
function renderChart() {
  const chart=$("equity-chart");chart.replaceChildren();
  const history=state.equity_history;
  function svg(tag,attrs,text){const e=document.createElementNS("http://www.w3.org/2000/svg",tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));if(text)e.textContent=text;chart.append(e);return e;}
  if(!history.length){svg("text",{x:20,y:90,class:"chart-text"},"La courbe apparaîtra après la première séance.");return;}
  const values=history.map(h=>Number(h.equity)), capital=Number(state.settings.capital);
  const min=Math.min(...values,capital)*0.998, max=Math.max(...values,capital)*1.002;
  const y=(v)=>150-(v-min)/(max-min)*125;
  svg("line",{x1:70,x2:820,y1:y(capital),y2:y(capital),class:"chart-baseline"});
  svg("text",{x:3,y:18,class:"chart-text"},usd(max));svg("text",{x:3,y:150,class:"chart-text"},usd(min));
  const points=values.map((v,i)=>(70+i/Math.max(values.length-1,1)*750)+","+y(v)).join(" ");
  svg("polyline",{points,class:"chart-line"});
  svg("text",{x:70,y:178,class:"chart-text"},history[0].date);
  svg("text",{x:820,y:178,"text-anchor":"end",class:"chart-text"},history.at(-1).date);
  $("chart-caption").textContent=history.length+" séances · "+(state.dataset?.synthetic?"cours fabriqués":"replay historique");
}
function renderJournal(){
  const filter=$("journal-filter").value;
  const rows=state.journal.filter(e=>filter==="all"||e.kind===filter||(filter==="error"&&e.kind==="worker_error"));
  $("journal-caption").textContent=state.journal_count+" événements archivés ; 250 derniers proposés ici";
  table("events",["Séance / réception","Événement","Détail vérifiable"],rows.map(e=>{
    const d=e.detail, detail=node("div");
    detail.append(node("span",[d.symbol,d.strategy?names[d.strategy]:null,d.reason||d.message||(d.status?statuses[d.status]:null)||(d.equity?"Valeur "+usd(d.equity):null)].filter(Boolean).join(" · ")));
    const expand=node("details"), pre=node("pre",JSON.stringify(d,null,2));expand.append(node("summary","Voir la preuve complète"),pre);detail.append(expand);
    return [e.day||e.recorded_at.slice(0,10),kinds[e.kind]||e.kind,detail];
  }),"Aucun événement pour ce filtre.");
}
const fields = {
  capital:["Capital fictif (USD)",1,100,1000000,0.01], risk_pct:["Risque par entrée (%)",100,0.1,2,0.1],
  max_exposure_pct:["Exposition par entrée (%)",100,1,25,1],max_total_risk_pct:["Risque cumulé prévu (%)",100,0.1,5,0.1],
  max_positions:["Positions au maximum",1,1,10,1],fee:["Frais par transaction (USD)",1,0,100,0.01],
  slippage_bps:["Glissement adverse (points de base)",1,0,100,1],max_gap_pct:["Gap maximal d’entrée (%)",100,0,10,0.5],
  max_hold:["Détention maximale (séances)",1,1,60,1]
};
function renderSettings(){
  if(!settingsRendered){
    $("settings-fields").replaceChildren();
    Object.entries(fields).forEach(([key,[label,scale,min,max,step]])=>{
      const wrapper=node("label",label),input=node("input");input.type="number";input.name=key;input.value=String(Number(state.settings[key])*scale);input.min=min;input.max=max;input.step=step;input.required=true;wrapper.append(input);$("settings-fields").append(wrapper);
    });settingsRendered=true;
  }
  $("settings-fields").querySelectorAll("input").forEach(i=>i.disabled=Boolean(state.cursor));
  $("settings-note").textContent=state.cursor?"Hypothèses figées. Pour une autre expérience, lancez l’application avec un nouveau chemin --db.":"Valeurs pédagogiques, pas des recommandations. Les frais s’appliquent à l’entrée et à la sortie.";
}
function render(){
  $("cursor").textContent=state.cursor||"Pas encore démarré";
  $("worker-status").textContent=state.worker.running?"Replay en cours · une séance / 0,5 s":state.worker.error?"Arrêt sur erreur : "+state.worker.error:"Moteur arrêté · reprise manuelle";
  $("data-tag").textContent=state.dataset?(state.dataset.synthetic?"Données synthétiques":"Import historique déclaré"):"Aucun jeu chargé";
  $("data-tag").className="tag"+(state.dataset?.synthetic?" synthetic":"");
  $("data-summary").textContent=state.dataset?state.dataset.source:"Chargez l’exemple synthétique ou importez votre propre fichier JSON validé.";
  $("data-details").replaceChildren();
  if(state.data.count){
    const items=[["Bougies D1",state.data.count+" · "+state.assets.map(a=>a.symbol).join(", ")],["Période",state.data.first+" au "+state.data.last],["Réception réelle",new Date(state.data.received_at).toLocaleString("fr-FR",{timeZoneName:"short"})],["Usage","Replay local ; aucune donnée temps réel"]];
    items.forEach(([k,v])=>$("data-details").append(node("dt",k),node("dd",v)));
  }
  const s=state.stats;
  $("metrics").replaceChildren();
  [["Valeur simulée",usd(s.equity)],["Espèces",usd(state.cash)],["Résultat réalisé",usd(s.realized)],["Résultat latent",usd(s.unrealized)],["Variation simulée",pct(s.return_pct)],["Drawdown clôture",pct(s.drawdown_pct)],["Transactions closes",s.trades],["Gagnantes / closes",s.wins+" / "+s.trades]].forEach(([label,value])=>{const metric=node("div",undefined,"metric");metric.append(node("span",label),node("strong",value));$("metrics").append(metric);});
  renderDecisions();renderChart();renderSettings();renderJournal();
  table("positions",["Titre","Quantité","Entrée","Clôture","Stop","Objectif","Séances"],state.positions.map(p=>[p.symbol,p.quantity,usd(p.entry),usd(p.mark),usd(p.stop),usd(p.target),p.age]),"Aucune position ouverte. Le capital reste en espèces jusqu’à une entrée admissible.");
  table("pending",["Titre","Stratégie","Signal","Stop","Objectif"],state.pending.map(p=>[p.symbol,names[p.strategy],p.signal_date,usd(p.stop),usd(p.target)]),"Aucune entrée en attente.");
  table("trades",["Titre","Entrée","Sortie","Quantité","Résultat net","Motif"],state.trades.slice().reverse().map(t=>[t.symbol,t.entry_date,t.exit_date,t.quantity,usd(t.pnl),node("span",t.exit_reason)]),"Aucune transaction terminée.");controls();
}
async function refresh(){try{const fresh=await api("/api/state");const current=JSON.stringify(fresh);state=fresh;if(current!==signature){signature=current;render();}}catch(error){message("Connexion locale indisponible : "+error.message,true);}}
$("fixture").addEventListener("click",()=>action("/api/fixture",{},"300 bougies synthétiques importées. Aucun cours réel."));
$("step").addEventListener("click",()=>action("/api/step",{expected_cursor:state.cursor},"Séance traitée et enregistrée."));
$("start").addEventListener("click",()=>action("/api/worker/start",{},"Replay local démarré."));
$("stop").addEventListener("click",()=>action("/api/worker/stop",{},"Replay en pause. Les résultats sont conservés."));
$("import-file").addEventListener("change",async(event)=>{const file=event.target.files[0];if(!file)return;try{if(file.size>10000000)throw new Error("Fichier trop volumineux : maximum 10 Mo");await action("/api/import",JSON.parse(await file.text()),"Import validé et enregistré.");}catch(error){message(error.message,true);}finally{event.target.value="";}});
$("settings").addEventListener("submit",event=>{event.preventDefault();const payload={};new FormData(event.target).forEach((value,key)=>{payload[key]=String(Number(value)/fields[key][1]);});action("/api/settings",payload,"Hypothèses enregistrées.");});
$("journal-filter").addEventListener("change",renderJournal);
document.querySelectorAll("nav a").forEach(a=>a.addEventListener("click",()=>{document.querySelectorAll("nav a").forEach(n=>n.classList.remove("active"));a.classList.add("active");}));
refresh();setInterval(()=>{if(!busy)refresh();},2500);
