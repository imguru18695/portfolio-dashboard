"""
patch_html.py  —  replaces the hardcoded JS data block in index.html
with live API fetch calls.  Run once from the project root.
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent
HTML = ROOT / "index.html"

NEW_SCRIPT = r"""<script>
// ============================================================ API + STATE
const API_BASE = '';
let holdings = [], filteredHoldings = [];
let currentFund = 'all', sortKey = null, sortDir = -1;
let summaryData = {}, chartInstances = {}, refreshTimer = null;

// ============================================================ FORMAT
const fmt  = v => v==null?'—':('$'+Math.abs(v).toLocaleString('en-US',{minimumFractionDigits:0,maximumFractionDigits:0}));
const fmtM = v => v==null?'—':('$'+(Math.abs(v)/1e6).toFixed(2)+'M');
const fmtN = v => v==null?'—':v.toLocaleString('en-US',{minimumFractionDigits:0,maximumFractionDigits:0});
const fmtP = v => v==null?'—':((v>=0?'+':'')+v.toFixed(2)+'%');
const cls  = v => v==null?'neutral':v>0?'positive':v<0?'negative':'neutral';
const sign = v => (v!=null&&v>=0)?'+':'−';

// ============================================================ LOADING
function showLoading(msg='Refreshing…') {
  const btn = document.getElementById('refresh-btn');
  if (btn) { btn.textContent = '↻ '+msg; btn.disabled = true; }
}
function hideLoading() {
  const btn = document.getElementById('refresh-btn');
  if (btn) { btn.textContent = '↻ Refresh Prices'; btn.disabled = false; }
}

// ============================================================ MAP API ROW
function mapRow(r) {
  return {
    ticker: r.ticker||'—', name: r.security_name||'—', fund: r.fund_name||'—',
    qty: r.quantity, price: r.current_price, mv: r.market_value,
    cost: r.cost_basis, upnl: r.unrealized_pnl, dpnl: r.daily_pnl,
    dpct: r.daily_pnl_pct, wt: r.weight_pct,
  };
}

// ============================================================ FETCH HELPERS
async function apiFetch(path) {
  const res = await fetch(API_BASE + path);
  if (!res.ok) throw new Error(path + ' failed: ' + res.status);
  return res.json();
}

// ============================================================ UPDATE KPI CARDS
function updateKPICards(s) {
  if (!s) return;
  const ef = s.funds?.find(f => f.fund_name==='Endowment Fund');
  const lh = s.funds?.find(f => f.fund_name==='Longhorn Fund');

  const set = (id, val) => { const el=document.getElementById(id); if(el) el.textContent=val; };
  const setClass = (id, cls) => { const el=document.getElementById(id); if(el) el.className=cls; };

  set('kpi-aum',    fmtM(s.combined_aum));
  set('kpi-daily',  (s.total_daily_pnl>=0?'+':'')+fmtM(s.total_daily_pnl).replace('$','')+' ('+fmtP(s.total_daily_pct)+')');
  setClass('kpi-daily', 'kpi-value '+cls(s.total_daily_pnl));
  set('kpi-upnl',   (s.total_unrealized>=0?'+':'')+fmtM(s.total_unrealized));
  setClass('kpi-upnl', 'kpi-value '+cls(s.total_unrealized));
  set('kpi-cost',   fmtM(s.total_cost_basis));
  set('kpi-pos',    s.total_positions+' positions · '+s.total_funds+' funds');
  set('kpi-unrealized-badge', '▲ +'+(s.total_unrealized/s.total_cost_basis*100).toFixed(2)+'% on cost');
  set('kpi-daily-badge', (s.total_daily_pnl>=0?'▲':'▼')+' '+fmtP(s.total_daily_pct)+' today');
  set('page-subtitle', 'As of '+(s.as_of_date||'—')+' · '+s.total_funds+' Funds · '+s.total_positions+' Positions');

  if (ef) {
    set('ef-mv',    fmtM(ef.market_value));
    set('ef-daily', (ef.daily_pnl>=0?'+':'')+fmt(ef.daily_pnl));  setClass('ef-daily','font-bold '+cls(ef.daily_pnl));
    set('ef-upnl',  (ef.unrealized_pnl>=0?'+':'')+fmtM(ef.unrealized_pnl)); setClass('ef-upnl','font-bold '+cls(ef.unrealized_pnl));
    set('ef-ret',   (ef.return_pct>=0?'+':'')+ef.return_pct?.toFixed(1)+'%'); setClass('ef-ret','font-bold '+cls(ef.return_pct));
    set('ef-pos',   ef.positions+' positions · ETF-based');
  }
  if (lh) {
    set('lh-mv',    fmtM(lh.market_value));
    set('lh-daily', (lh.daily_pnl>=0?'+':'')+fmt(lh.daily_pnl));  setClass('lh-daily','font-bold '+cls(lh.daily_pnl));
    set('lh-upnl',  (lh.unrealized_pnl>=0?'+':'')+fmtM(lh.unrealized_pnl)); setClass('lh-upnl','font-bold '+cls(lh.unrealized_pnl));
    set('lh-ret',   (lh.return_pct>=0?'+':'')+lh.return_pct?.toFixed(1)+'%'); setClass('lh-ret','font-bold '+cls(lh.return_pct));
    set('lh-pos',   lh.positions+' positions · Equity');
  }
}

// ============================================================ HOLDINGS TABLE
function renderHoldings() {
  const body = document.getElementById('holdings-body');
  if (!body) return;
  body.innerHTML = filteredHoldings.map(r => `
    <tr>
      <td class="td-ticker">${r.ticker}</td>
      <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${r.name}">${r.name}</td>
      <td><span style="font-size:11px;font-weight:600;padding:2px 8px;border-radius:10px;
        background:${r.fund==='Endowment Fund'?'#fff7ed':'#eff6ff'};
        color:${r.fund==='Endowment Fund'?'#c2410c':'#1d4ed8'}">
        ${r.fund==='Endowment Fund'?'Endowment':'Longhorn'}</span></td>
      <td class="td-right">${fmtN(r.qty)}</td>
      <td class="td-right">${r.price!=null?'$'+r.price.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):'—'}</td>
      <td class="td-right">${fmtM(r.mv)}</td>
      <td class="td-right">${fmtM(r.cost)}</td>
      <td class="td-right ${cls(r.upnl)}">${sign(r.upnl)}${fmt(r.upnl)}</td>
      <td class="td-right ${cls(r.dpnl)}">${sign(r.dpnl)}${fmt(r.dpnl)}</td>
      <td class="td-right ${cls(r.dpct)}">${fmtP(r.dpct)}</td>
      <td class="td-right">${r.wt!=null?r.wt.toFixed(2)+'%':'—'}</td>
    </tr>`).join('');
  const el = document.getElementById('holdings-count');
  if (el) el.textContent = filteredHoldings.length+' positions';
}

function filterFund(fund, el) {
  currentFund = fund;
  document.querySelectorAll('.fund-tab').forEach(t=>t.classList.remove('active'));
  el.classList.add('active');
  applyFilters();
}
function searchHoldings(q) { applyFilters(q); }
function applyFilters(q = document.querySelector('.search-box')?.value||'') {
  filteredHoldings = holdings.filter(r => {
    const mf = currentFund==='all'||(currentFund==='endowment'&&r.fund==='Endowment Fund')||(currentFund==='longhorn'&&r.fund==='Longhorn Fund');
    const mq = !q||r.ticker.toLowerCase().includes(q.toLowerCase())||r.name.toLowerCase().includes(q.toLowerCase());
    return mf&&mq;
  });
  renderHoldings();
}
function sortTable(key) {
  if (sortKey===key) sortDir*=-1; else { sortKey=key; sortDir=-1; }
  filteredHoldings.sort((a,b)=>{
    const av=a[key],bv=b[key];
    if(av==null)return 1; if(bv==null)return-1;
    return typeof av==='string'?av.localeCompare(bv)*sortDir:(av-bv)*sortDir;
  });
  renderHoldings();
}

// ============================================================ TOP MOVERS
function renderTopMovers(movers) {
  const body = document.getElementById('top-movers-body');
  if (!body) return;
  body.innerHTML = movers.map(r => `
    <tr>
      <td class="td-ticker">${r.ticker}</td>
      <td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${r.name}</td>
      <td><span style="font-size:11px;font-weight:600;padding:2px 7px;border-radius:10px;
        background:${r.fund==='Endowment Fund'?'#fff7ed':'#eff6ff'};
        color:${r.fund==='Endowment Fund'?'#c2410c':'#1d4ed8'}">
        ${r.fund==='Endowment Fund'?'Endowment':'Longhorn'}</span></td>
      <td class="td-right ${cls(r.dpnl)}">${sign(r.dpnl)}${fmt(r.dpnl)}</td>
      <td class="td-right ${cls(r.dpct)}">${fmtP(r.dpct)}</td>
    </tr>`).join('');
}

// ============================================================ P&L LIST
function renderPnlList(data) {
  const lh = data.filter(r=>r.fund==='Longhorn Fund').sort((a,b)=>(b.upnl||0)-(a.upnl||0)).slice(0,12);
  const maxVal = Math.max(...lh.map(r=>Math.abs(r.upnl||0)),1);
  const el = document.getElementById('pnl-list');
  if (!el) return;
  el.innerHTML = lh.map(r=>{
    const pct = Math.min(Math.abs(r.upnl||0)/maxVal*100,100);
    return `<div>
      <div class="flex justify-between text-xs mb-1">
        <span class="font-semibold text-slate-700">${r.ticker}</span>
        <span class="${cls(r.upnl)} font-semibold">${sign(r.upnl)}${fmt(r.upnl)}</span>
      </div>
      <div style="height:5px;background:#f1f5f9;border-radius:3px;">
        <div style="height:100%;width:${pct}%;background:${(r.upnl||0)>=0?'#22c55e':'#ef4444'};border-radius:3px;"></div>
      </div></div>`;
  }).join('');
}

// ============================================================ CHARTS
function destroyChart(id) { if(chartInstances[id]){chartInstances[id].destroy();delete chartInstances[id];} }

function initCharts(data, s) {
  Chart.register(ChartDataLabels);
  const orange='#BF5700',navy='#1e3a5f',grid='#f1f5f9',muted='#64748b';
  const ef=s.funds?.find(f=>f.fund_name==='Endowment Fund');
  const lh=s.funds?.find(f=>f.fund_name==='Longhorn Fund');

  // AUM Bar
  destroyChart('aumBarChart');
  chartInstances['aumBarChart']=new Chart(document.getElementById('aumBarChart'),{
    type:'bar',data:{labels:['Endowment Fund','Longhorn Fund'],
      datasets:[{data:[ef?(ef.market_value/1e6).toFixed(2):0,lh?(lh.market_value/1e6).toFixed(2):0],
        backgroundColor:[orange,navy],borderRadius:6,borderSkipped:false}]},
    options:{responsive:true,maintainAspectRatio:true,plugins:{legend:{display:false},
      datalabels:{color:'#fff',font:{weight:'bold',size:11},formatter:v=>'$'+v+'M'}},
      scales:{x:{grid:{display:false},ticks:{color:muted,font:{size:11}}},
              y:{grid:{color:grid},ticks:{color:muted,font:{size:10},callback:v=>'$'+v+'M'},beginAtZero:true}}}
  });

  // Fund Donut
  const efP=s.combined_aum?+((ef?.market_value||0)/s.combined_aum*100).toFixed(2):52;
  const lhP=s.combined_aum?+((lh?.market_value||0)/s.combined_aum*100).toFixed(2):48;
  destroyChart('fundDonut');
  chartInstances['fundDonut']=new Chart(document.getElementById('fundDonut'),{
    type:'doughnut',data:{labels:['Endowment Fund','Longhorn Fund'],
      datasets:[{data:[efP,lhP],backgroundColor:[orange,navy],borderWidth:0}]},
    options:{responsive:true,maintainAspectRatio:true,cutout:'65%',
      plugins:{legend:{position:'bottom',labels:{font:{size:11},padding:12,color:'#374151'}},
               datalabels:{color:'#fff',font:{weight:'bold',size:11},formatter:v=>v.toFixed(1)+'%'}}}
  });

  // P&L Stacked Bar
  destroyChart('pnlBar');
  chartInstances['pnlBar']=new Chart(document.getElementById('pnlBar'),{
    type:'bar',data:{labels:['Endowment','Longhorn'],
      datasets:[{label:'Cost Basis',data:[ef?(ef.cost_basis/1e6).toFixed(2):0,lh?(lh.cost_basis/1e6).toFixed(2):0],backgroundColor:'#e2e8f0',borderRadius:4},
                {label:'Unrealized P&L',data:[ef?(ef.unrealized_pnl/1e6).toFixed(2):0,lh?(lh.unrealized_pnl/1e6).toFixed(2):0],backgroundColor:'#22c55e',borderRadius:4}]},
    options:{responsive:true,maintainAspectRatio:true,
      plugins:{legend:{position:'bottom',labels:{font:{size:10},padding:8}},datalabels:{display:false}},
      scales:{x:{stacked:true,grid:{display:false},ticks:{color:muted,font:{size:11}}},
              y:{stacked:true,grid:{color:grid},ticks:{color:muted,font:{size:10},callback:v=>'$'+v+'M'},beginAtZero:true}}}
  });

  // Endowment Donut (breakdown)
  const efRows=data.filter(r=>r.fund==='Endowment Fund');
  const c12=['#BF5700','#1e3a5f','#3b82f6','#22c55e','#f59e0b','#8b5cf6','#06b6d4','#ef4444','#64748b','#84cc16','#f97316','#0ea5e9'];
  destroyChart('endowmentPie');
  chartInstances['endowmentPie']=new Chart(document.getElementById('endowmentPie'),{
    type:'doughnut',data:{labels:efRows.map(r=>r.ticker),
      datasets:[{data:efRows.map(r=>r.wt||0),backgroundColor:c12,borderWidth:1,borderColor:'#fff'}]},
    options:{responsive:true,maintainAspectRatio:false,cutout:'55%',
      plugins:{legend:{position:'right',labels:{font:{size:10},padding:6}},
               datalabels:{color:'#fff',font:{weight:'bold',size:9},formatter:(v,ctx)=>v>3?ctx.chart.data.labels[ctx.dataIndex]:''}}}
  });

  // Longhorn Top 15 (breakdown)
  const lhTop=data.filter(r=>r.fund==='Longhorn Fund').sort((a,b)=>(b.mv||0)-(a.mv||0)).slice(0,15);
  destroyChart('longhornBar');
  chartInstances['longhornBar']=new Chart(document.getElementById('longhornBar'),{
    type:'bar',data:{labels:lhTop.map(r=>r.ticker),
      datasets:[{data:lhTop.map(r=>+((r.mv||0)/1e6).toFixed(2)),
        backgroundColor:lhTop.map(r=>(r.upnl||0)>=0?navy:'#ef4444'),borderRadius:4}]},
    options:{responsive:true,maintainAspectRatio:false,indexAxis:'y',
      plugins:{legend:{display:false},datalabels:{color:'#fff',font:{size:9},formatter:v=>'$'+v+'M'}},
      scales:{x:{grid:{color:grid},ticks:{color:muted,font:{size:9},callback:v=>'$'+v+'M'}},
              y:{grid:{display:false},ticks:{color:muted,font:{size:10,weight:'bold'}}}}}
  });

  // Bubble chart
  const top30=data.slice(0,30);
  destroyChart('bubbleChart');
  chartInstances['bubbleChart']=new Chart(document.getElementById('bubbleChart'),{
    type:'bubble',
    data:{datasets:[{data:top30.map(r=>({x:r.wt||0,y:r.dpct||0,r:Math.sqrt(Math.abs(r.mv||0)/1e5)*1.5,label:r.ticker,dpnl:r.dpnl})),
      backgroundColor:top30.map(r=>(r.dpct||0)>=0?'rgba(34,197,94,0.6)':'rgba(239,68,68,0.6)'),
      borderColor:top30.map(r=>(r.dpct||0)>=0?'#16a34a':'#dc2626'),borderWidth:1}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},datalabels:{display:false},
      tooltip:{callbacks:{label:ctx=>{const d=ctx.raw;return `${d.label}: ${fmtP(d.y)} (${sign(d.dpnl)}${fmt(d.dpnl)})`;}}}},
      scales:{x:{title:{display:true,text:'Portfolio Weight (%)',font:{size:11}},grid:{color:grid},ticks:{color:muted,callback:v=>v+'%'}},
              y:{title:{display:true,text:'Daily Change (%)',font:{size:11}},grid:{color:grid},ticks:{color:muted,callback:v=>v+'%'}}}}
  });
}

// ============================================================ STATUS BAR
async function updateStatusBar() {
  try {
    const s = await apiFetch('/api/status');
    document.getElementById('market-status').textContent = s.is_market_hours ? 'Market Open' : 'Market Closed';
    document.getElementById('market-dot').className = 'status-dot '+(s.is_market_hours?'dot-live':'dot-closed');
    if (s.prices_refreshed_at) {
      document.getElementById('last-refresh').textContent =
        new Date(s.prices_refreshed_at).toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'})+' local';
    }
  } catch(e) { console.warn('Status fetch failed', e); }
}

// ============================================================ LOAD DATA
async function loadData(force=false) {
  showLoading(force?'Refreshing…':'Loading…');
  try {
    if (force) await fetch(API_BASE+'/api/refresh',{method:'POST'});
    const [data, s, moversJson] = await Promise.all([
      apiFetch('/api/portfolio').then(j=>j.data.map(mapRow)),
      apiFetch('/api/summary'),
      apiFetch('/api/top-movers?n=8').then(j=>j.data.map(mapRow)),
    ]);
    holdings = data;
    summaryData = s;
    updateKPICards(s);
    applyFilters();
    renderTopMovers(moversJson);
    renderPnlList(data);
    initCharts(data, s);
    await updateStatusBar();
  } catch(err) {
    console.error('[Dashboard] Load error:', err);
    document.getElementById('last-refresh').textContent = 'Error — see console';
  } finally { hideLoading(); }
}

async function refreshData() { await loadData(true); }

// ============================================================ NAV
function showPage(page, el) {
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('nav .nav-item').forEach(n=>n.classList.remove('active'));
  document.getElementById('page-'+page).classList.add('active');
  if (el) el.classList.add('active');
  const titles={
    summary:    ['Portfolio Summary',''],
    holdings:   ['Holdings','Full position detail across all funds'],
    breakdown:  ['Breakdown & Allocation','Portfolio composition by fund'],
    analytics:  ['Analytics','Risk metrics & performance attribution'],
    constraints:['Constraints Monitor','Portfolio rule monitoring & breach alerts'],
  };
  document.getElementById('page-title').textContent = titles[page][0];
  if (page!=='summary') document.getElementById('page-subtitle').textContent = titles[page][1];
  window.scrollTo(0,0);
}

// ============================================================ BOOT
document.addEventListener('DOMContentLoaded', () => {
  loadData(false);
  setInterval(()=>loadData(false), 5*60*1000);  // auto-refresh every 5 min
});
</script>"""

content = HTML.read_text(encoding="utf-8")
tag     = '<script>\n// ============================================================ DATA'
start   = content.find(tag)
end     = content.find('</script>', start) + len('</script>')
HTML.write_text(content[:start] + NEW_SCRIPT + content[end:], encoding="utf-8")
print(f"Done. File size: {HTML.stat().st_size:,} bytes")
