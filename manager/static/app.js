const state={view:'overview',clients:[],releases:[],deployments:[],tickets:[],audit:[],summary:{},settings:{storageBackend:'sqlite',github:{},repositories:{}},githubReleases:[],key:'',wizardStep:1,wizardClientId:'',wizardBootstrapToken:'',wizardPreflight:null,wizardJobs:[],clientView:localStorage.getItem('vtab_client_view')||'grid',deploymentHistoryCollapsed:localStorage.getItem('vtab_deployment_history_collapsed')==='true',offerClientIds:new Set(),offerSearch:'',offerMode:'update',offerAllowedClientIds:new Set(),offerNonce:''};
const titles={
  overview:['Overview','Deployment operations and client environment health'],
  deployments:['Deployments','Plan, approve, and monitor controlled client rollouts'],
  releases:['Release catalog','Verified artifacts available to deployment plans'],
  clients:['Client environments','Operational inventory and connector health'],
  tickets:['Support tickets','Client-approved issues and delivery status'],
  settings:['Settings','Manager preferences, security planning, and audit history']
};
const $=id=>document.getElementById(id);
const icon=name=>`<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const esc=value=>String(value??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const stamp=value=>value?new Date(value).toLocaleString():'—';
const versionLabel=value=>{const v=String(value||'').trim();return !v?'Not specified':v.startsWith('commit-')?`Commit ${v.slice(7,19)}`:v.startsWith('v')?v:`v${v}`};
const componentLabel=value=>value==='services'?'Reporting Services':value==='administration'?'Admin Console':String(value||'Unknown');
const action=(kind,iconName,label,attribute,id,title=label)=>`<button type="button" class="table-action action-${kind}" ${attribute}="${esc(id)}" title="${esc(title)}" aria-label="${esc(label)}">${icon(iconName)}<span>${esc(label)}</span></button>`;
const iconAction=(kind,iconName,label,attribute,id,title=label)=>`<button type="button" class="table-action action-${kind} icon-only" ${attribute}="${esc(id)}" title="${esc(title)}" aria-label="${esc(label)}">${icon(iconName)}</button>`;

const badge=value=>{
  const raw=String(value||'');
  const lower=raw.toLowerCase();
  const label=lower==='scheduled'||lower==='offered'?'Offered':((lower==='succeeded'||lower==='completed')?'Completed':(lower==='pending_approval'?'Pending approval':raw.replaceAll('_',' ')));
  const cls=lower==='scheduled'||lower==='offered'?'offered':((lower==='succeeded'||lower==='completed')?'succeeded':(lower==='pending_approval'?'pending':lower));
  return `<span class="badge ${esc(cls)}">${esc(label)}</span>`;
};
const empty=(text,action='')=>`<div class="empty"><span>${icon('box')}</span><b>${esc(text)}</b>${action}</div>`;

async function api(path,options={}){
  const response=await fetch(path,{cache:'no-store',...options,headers:{'Content-Type':'application/json','X-VTAB-Manager-Key':state.key,...(options.headers||{})}});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(data.detail||`Request failed (${response.status})`);
  return data;
}
function notify(message,error=false){const node=$('notice');node.textContent=message;node.className=`notice${error?' error':''}`;node.hidden=false;clearTimeout(notify.timer);notify.timer=setTimeout(()=>node.hidden=true,5500)}
function setBusy(button,busy,label='Working…'){if(!button)return;if(busy){button.dataset.label=button.innerHTML;button.disabled=true;button.innerHTML=`<span class="button-spinner"></span>${label}`}else{button.disabled=false;if(button.dataset.label)button.innerHTML=button.dataset.label}}

function applyTheme(theme){document.documentElement.dataset.theme=theme;localStorage.setItem('vtab_manager_theme',theme);document.querySelectorAll('[data-theme-choice]').forEach(button=>button.classList.toggle('selected',button.dataset.themeChoice===theme))}
function toggleTheme(){applyTheme(document.documentElement.dataset.theme==='dark'?'light':'dark')}
function toggleMobile(open){$('sidebar').classList.toggle('mobile-open',open);$('mobileShade').classList.toggle('visible',open)}

async function bootstrap(){
  try{
    const config=await fetch('/api/v1/config').then(r=>r.json());
    state.key=localStorage.getItem('vtab_manager_key')||config.developmentKey||prompt('Enter the VTAB Manager operator key')||'';
    localStorage.setItem('vtab_manager_key',state.key);
    $('versionLabel').textContent=`Manager ${config.version}`;
    $('connectionLabel').textContent='Manager online';
    await loadAll();
    setInterval(async ()=>{
      const activeStates=['deploying','validating','preflight','scheduled','apply_requested','offered','pending_approval'];
      const hasActive=(state.deployments||[]).some(d=>activeStates.includes(d.status));
      if(hasActive && ['overview','deployments'].includes(state.view)){
        try{
          const res=await api('/api/v1/operator/deployments');
          state.deployments=res.deployments||[];
          render();
        }catch(e){}
      }
    },4000);
  }catch(error){$('connectionLabel').textContent='Connection failed';notify(error.message,true);$('content').className='content';$('content').innerHTML=empty(error.message)}
}
async function loadAll(){
  $('content').className='content loading';$('content').innerHTML='<div class="loader"></div><p>Loading manager data…</p>';
  try{
    const [summary,clients,releases,deployments,tickets,audit,managerSettings]=await Promise.all([
      api('/api/v1/operator/summary'),api('/api/v1/operator/clients'),api('/api/v1/operator/releases'),api('/api/v1/operator/deployments'),api('/api/v1/operator/tickets'),api('/api/v1/operator/audit?limit=250'),api('/api/v1/operator/settings')
    ]);
    Object.assign(state,{summary,clients:clients.clients,releases:releases.releases,deployments:deployments.deployments,tickets:tickets.tickets,audit:audit.events,settings:managerSettings});
    $('ticketCount').textContent=state.tickets.filter(ticket=>ticket.status!=='closed').length;
    render();
    if(state.settings.github?.configured)loadGitHubReleases(false);
  }catch(error){notify(error.message,true);if(/operator key/i.test(error.message))localStorage.removeItem('vtab_manager_key');throw error}
}
function setView(view){
  state.view=view;document.querySelectorAll('.nav-item').forEach(button=>button.classList.toggle('active',button.dataset.view===view));
  const [title,subtitle]=titles[view];$('pageTitle').textContent=title;$('pageSubtitle').textContent=subtitle;toggleMobile(false);render();
}
function render(){
  $('content').className='content';$('primaryAction').onclick=openDeployment;
  const renderer={overview:renderOverview,deployments:renderDeployments,releases:renderReleases,clients:renderClients,tickets:renderTickets,settings:renderSettings}[state.view];
  $('content').innerHTML=renderer();bindActions();
}
function panel(title,subtitle,body,extraClass='',actions=''){return `<article class="panel ${extraClass}"><div class="panel-head"><div><h2>${esc(title)}</h2><p>${esc(subtitle)}</p></div>${actions}</div><div class="panel-body">${body}</div></article>`}
function viewTitle(title,subtitle,action=''){return `<div class="view-title"><div><h2>${esc(title)}</h2><p>${esc(subtitle)}</p></div>${action}</div>`}
function metric(iconName,label,value,tone,detail){return `<article class="metric ${tone}"><div class="metric-icon">${icon(iconName)}</div><div class="metric-copy"><small>${esc(label)}</small><strong>${esc(value)}</strong><span>${esc(detail)}</span></div></article>`}

function renderOverview(){
  const s=state.summary;const attention=s.deployments_attention||0;const openTickets=s.open_tickets||0;const healthy=state.clients.filter(client=>client.last_health_status==='healthy').length;
  return `<section class="welcome-card"><div><span class="eyebrow">OPERATIONS CENTER</span><h2>Ready for the next controlled rollout.</h2><p>Register the target client, select a verified release, and schedule deployment from one guided workflow.</p><button class="primary inline-deploy" data-open-deployment>${icon('rocket')} New deployment</button></div><div class="readiness-orbit"><div><strong>${state.clients.length?Math.round(healthy/state.clients.length*100):100}%</strong><span>environment health</span></div><i></i><i></i></div></section>
  <div class="metric-grid">${metric('building','Active clients',s.clients||0,'cyan',`${healthy} reporting healthy`)}${metric('box','Available releases',s.available_releases||0,'purple','Published and verified')}${metric('rocket','Need attention',attention,'amber',attention?'Review deployment jobs':'Rollouts on track')}${metric('ticket','Open tickets',openTickets,'pink',openTickets?'Client follow-up required':'Support queue clear')}</div>
  <div class="overview-grid">${panel('Recent deployments','Latest rollout activity across environments',deploymentTable(s.recent_deployments||[],true),'deployment-panel')}${renderPostureCard()}</div>
  ${panel('Recent support activity','Client-approved escalations only',ticketTable(s.recent_tickets||[],true),'support-panel')}`;
}
function renderPostureCard(){
  const total=state.clients.length;const healthy=state.clients.filter(client=>client.last_health_status==='healthy').length;const available=state.releases.filter(release=>release.status==='available').length;
  return `<article class="panel posture-card"><div class="panel-head"><div><h2>Deployment posture</h2><p>Preflight readiness snapshot</p></div><span class="live-pill"><i></i>Live</span></div><div class="posture-body"><div class="posture-score"><span>${total?Math.round(healthy/total*100):100}</span><small>Health score</small></div><div class="check-list"><div>${icon('check')}<span><b>Client connectors</b><small>${healthy}/${total||0} healthy</small></span></div><div>${icon('check')}<span><b>Verified releases</b><small>${available} available</small></span></div><div>${icon('shield')}<span><b>Approval gate</b><small>Enforced for all deployments</small></span></div></div></div></article>`;
}
function releaseDetails(row){try{return JSON.parse(row.validation_details_json||'{}')}catch(error){return {}}}
function newestReleaseIds(rows=state.releases){
  const ids=new Map();
  const approved=[...rows].filter(row=>row.status==='available'&&row.validation_status==='approved');
  const source = approved.length ? approved : [...rows];
  source.sort((a,b)=>new Date(b.validated_at||b.created_at||0)-new Date(a.validated_at||a.created_at||0))
    .forEach(row=>{if(!ids.has(row.component))ids.set(row.component,row.id)});
  return ids;
}
function liveReleaseIds(){const ids=new Map();[...state.deployments].filter(row=>['succeeded','completed'].includes(row.status)).sort((a,b)=>new Date(b.finished_at||b.updated_at||b.created_at||0)-new Date(a.finished_at||a.updated_at||a.created_at||0)).forEach(row=>{if(!ids.has(row.component))ids.set(row.component,row.release_id)});for(const component of ['services','administration']){if(ids.has(component))continue;const reported=state.clients.map(client=>component==='services'?client.installed_services_version:client.installed_admin_version).find(Boolean);if(!reported)continue;const match=state.releases.find(row=>row.component===component&&(row.version===reported||`v${row.version}`===reported||(row.git_commit_sha&&reported.includes(row.git_commit_sha.slice(0,7)))));if(match)ids.set(component,match.id)}return ids}
function previousLiveReleaseIds(){const live=liveReleaseIds();const result=new Map();[...state.deployments].filter(row=>['succeeded','completed'].includes(row.status)).sort((a,b)=>new Date(b.finished_at||b.updated_at||b.created_at||0)-new Date(a.finished_at||a.updated_at||a.created_at||0)).forEach(row=>{if(!result.has(row.component)&&row.release_id!==live.get(row.component))result.set(row.component,row.release_id)});for(const component of ['services','administration']){if(result.has(component))continue;const rows=state.releases.filter(row=>row.component===component&&row.status==='available'&&row.validation_status==='approved').sort((a,b)=>new Date(b.validated_at||b.created_at||0)-new Date(a.validated_at||a.created_at||0));const index=rows.findIndex(row=>row.id===live.get(component));if(index>=0&&rows[index+1])result.set(component,rows[index+1].id)}return result}
function rollbackClientIds(row,live){const liveRelease=state.releases.find(item=>item.id===live.get(row.component));if(!liveRelease)return [];return state.clients.filter(client=>{const installed=row.component==='services'?client.installed_services_version:client.installed_admin_version;return installed===liveRelease.version||installed===`v${liveRelease.version}`||(liveRelease.git_commit_sha&&String(installed||'').includes(liveRelease.git_commit_sha.slice(0,7)))}).map(client=>client.id)}
function pendingReleaseTable(rows){
  if(!rows.length)return empty('No incoming commits are waiting for approval.');
  const newest=new Set([...rows].sort((a,b)=>Date.parse(b.created_at||0)-Date.parse(a.created_at||0)).filter((r,i,arr)=>arr.findIndex(v=>v.component===r.component)===i).map(r=>r.id));
  return `<div class="table-wrap"><table><thead><tr><th>Application</th><th>Version</th><th>Commit</th><th>Change summary</th><th>Author</th><th>Received</th><th>Status</th><th>Review</th></tr></thead><tbody>${rows.map(row=>{
    const details=releaseDetails(row);
    return `<tr><td><div class="component-cell">${icon('code-branch')}<strong>${esc(componentLabel(row.component))}</strong>${newest.has(row.id)?'<span class="release-tag new">NEW</span>':''}</div></td><td><span class="version-chip">${esc(versionLabel(row.version))}</span></td><td><a class="commit-link" href="${esc(row.artifact_url||'#')}" target="_blank" rel="noopener noreferrer">${esc((row.git_commit_sha||'').slice(0,8))}</a></td><td><strong>${esc(row.release_notes||details.message||'GitHub commit')}</strong></td><td>${esc(details.author||'Unknown author')}</td><td>${stamp(row.created_at)}</td><td>${badge('pending_approval')}</td><td><div class="row-actions">${iconAction('neutral','tag','Set version','data-edit-version',row.id,'Set the exact application version before approval')}${iconAction('approve','check-circle','Approve','data-approve-release',row.id)}${iconAction('reject','x-circle','Reject','data-reject-release',row.id)}</div></td></tr>`;
  }).join('')}</tbody></table></div>`;
}
function deploymentHistoryPanel(){const collapsed=state.deploymentHistoryCollapsed;return `<article class="panel deployment-history-panel ${collapsed?'collapsed':''}"><div class="panel-head"><div><h2>Deployment history</h2><p>Read-only rollout, health, and rollback activity</p></div><button class="history-toggle" type="button" data-toggle-deployment-history aria-expanded="${!collapsed}">${icon('chevron-down')}<span>${collapsed?'Show history':'Hide history'}</span></button></div><div class="collapsible-panel-body">${deploymentTable(state.deployments,false)}</div></article>`}
function renderDeployments(){
  const pending=state.releases.filter(row=>row.status==='pending_approval');
  const drafts=state.releases.filter(row=>row.status==='draft'&&row.validation_status!=='approved');
  const draftTable=drafts.length?panel('Manual release drafts','Drafts are not published until their validation checks are approved',`<div class="table-wrap"><table><thead><tr><th>Application</th><th>Version</th><th>Status</th><th>Action</th></tr></thead><tbody>${drafts.map(r=>`<tr><td>${esc(componentLabel(r.component))}</td><td><span class="version-chip">${esc(versionLabel(r.version))}</span></td><td>${badge('draft')}</td><td>${action('approve','shield','Validate release','data-validate-release',r.id)}</td></tr>`).join('')}</tbody></table></div>`):'';
  const actions=`<div class="row-actions"><button class="secondary view-action" data-sync-github>${icon('refresh')} Sync GitHub</button></div>`;
  return `${viewTitle('Deployment operations','Review incoming commits, deploy releases to clients, and verify connector health',actions)}${renderGitHubReleaseSource()}${panel('Incoming commits','This is the only approval gate. Approved commits move automatically into Releases.',pendingReleaseTable(pending),'pending-releases')}${draftTable}${deploymentHistoryPanel()}`;
}
function deploymentTargetUrl(row){
  if(!row) return '';
  const notes=(()=>{try{return JSON.parse(row.agent_notes||'{}')}catch(e){return {}}})();
  let url = (notes.provider?.url || '').trim();
  if(!url || url.includes('admin-reporting.vtabsquare.com')){
    const client=(state.clients||[]).find(c=>c.id===row.client_id);
    if(row.component==='services') url = client?.services_url || 'https://reportservices.vtabsquare.com';
    else if(row.component==='administration') url = (client?.admin_url && !client.admin_url.includes('admin-reporting.vtabsquare.com')) ? client.admin_url : 'https://admin-reporting.139.59.32.39.nip.io';
  }
  if(url){
    url = url.replace(/[`'"]/g, '').trim();
    if(url && !/^https?:\/\//i.test(url)) url = 'https://' + url;
  }
  return url;
}
function deploymentVersion(row){
  if(!row) return '';
  const rel = (state.releases||[]).find(r=>r.id===row.release_id);
  const relVer = rel?.version || row.release_version;
  if(relVer && !relVer.startsWith('commit-')) return relVer;
  if(row.target_version && !row.target_version.startsWith('commit-')) return row.target_version;
  return relVer || row.target_version;
}
function deploymentTable(rows,compact){
  if(!rows.length)return empty('No deployment jobs yet.',`<button class="text-action" data-open-deployment>Create the first deployment</button>`);
  return `<div class="table-wrap"><table><thead><tr><th>Client</th><th>Application</th><th>Application version</th><th>Commit</th><th>Status</th><th>Live App</th>${compact?'':'<th>Created</th>'}</tr></thead><tbody>${rows.map(row=>{
    const targetUrl = deploymentTargetUrl(row);
    const isCompleted = ['succeeded', 'completed'].includes(row.status);
    const liveLink = (isCompleted && targetUrl)
      ? `<a class="live-app-badge" href="${esc(targetUrl)}" target="_blank" rel="noopener noreferrer" title="Open ${esc(targetUrl)} in a new tab">${icon('external-link')}<span>Open ↗</span></a>`
      : `<span style="color:var(--text-dim,#64748b);font-size:12px">—</span>`;
    const rel = (state.releases||[]).find(r=>r.id===row.release_id);
    const commitSha = row.git_commit_sha || rel?.git_commit_sha || ((row.target_version||'').startsWith('commit-') ? row.target_version.slice(7) : '');
    const repoUrl = row.repository_url || rel?.repository_url || (row.component === 'services' ? (state.settings.repositories?.services_repository || 'https://github.com/vtabsquare/Reportingtool-webservice') : (state.settings.repositories?.admin_repository || 'https://github.com/vtabsquare/Reporting-admin'));
    const cleanRepo = String(repoUrl||'').replace(/\.git$/,'');
    const commitLink = commitSha ? `<a class="commit-link" href="${esc(cleanRepo)}/commit/${esc(commitSha)}" target="_blank" rel="noopener noreferrer">${esc(commitSha.slice(0,8))}</a>` : `<span style="color:var(--text-dim,#64748b);font-size:12px">—</span>`;
    return `<tr><td><div class="client-name"><span class="client-logo">${esc((row.client_name||'C')[0])}</span><strong>${esc(row.client_name)}</strong></div></td><td><div class="component-cell">${icon('code-branch')}<strong>${esc(componentLabel(row.component))}</strong></div></td><td><span class="version-chip">${esc(versionLabel(deploymentVersion(row)))}</span>${row.deployment_type==='rollback'?'<span class="release-tag rollback-label">ROLLBACK</span>':''}</td><td>${commitLink}</td><td>${badge(row.status)}</td><td>${liveLink}</td>${compact?'':`<td>${stamp(row.created_at)}</td>`}</tr>`;
  }).join('')}</tbody></table></div>`;
}
function renderReleases(){
  const approved=state.releases.filter(row=>['available','superseded'].includes(row.status)&&row.validation_status==='approved');
  return `${viewTitle('Release catalog','Only operator-approved versions appear here; one latest verified release per application is active',`<div class="row-actions"><button class="secondary view-action" data-sync-github>${icon('refresh')} Sync GitHub</button><button class="secondary view-action" data-open-release>${icon('plus')} Create release</button></div>`)}${renderGitHubReleaseSource()}${panel('Verified release registry','Approved application versions, latest deployment state, and one-step rollback',releaseTable(approved))}`;
}
function renderGitHubReleaseSource(){
  const repos=state.settings.repositories||{};
  const srvRepo = repos.services_repository || state.settings.github?.repository || 'Not configured';
  const admRepo = repos.admin_repository || 'Not configured';
  return `<article class="source-panel"><div class="source-panel-head"><div class="source-icon">${icon('box')}</div><div><b>Services:</b> <span>${esc(srvRepo)}</span><br><b>Administration:</b> <span>${esc(admRepo)}</span><br><small style="color:var(--text-dim, #6e7681)">Click refresh to synchronize latest Git commits from main</small></div><button class="icon-button" data-refresh-github title="Sync latest commits from GitHub">${icon('refresh')}</button></div></article>`;
}
function releaseTable(rows){
  if(!rows.length)return empty('No approved releases are available yet. Approve an incoming commit in Deployments.');
  const live=liveReleaseIds(),previous=previousLiveReleaseIds(),newest=newestReleaseIds(rows.filter(row=>row.status==='available'&&row.validation_status==='approved'));
  return `<div class="table-wrap"><table><thead><tr><th>Application</th><th>Version</th><th>Commit</th><th>Validation</th><th>Approved</th><th>Actions</th></tr></thead><tbody>${rows.map(row=>{
    const isActive=live.get(row.component)===row.id;const isNewest=newest.get(row.component)===row.id;const canDeploy=row.status==='available'&&row.validation_status==='approved'&&isNewest&&!isActive;const canRollback=previous.get(row.component)===row.id&&Boolean(live.get(row.component));const rollbackClients=canRollback?rollbackClientIds(row,live):[];
    
    return `<tr class="${isActive?'active-release-row':''}"><td><div class="component-cell">${icon('code-branch')}<strong>${esc(componentLabel(row.component))}</strong>${isActive?'<span class="release-tag live">LIVE</span>':''}${isNewest?'<span class="release-tag new">NEW</span>':''}</div></td><td><span class="version-chip">${esc(versionLabel(row.version))}</span></td><td><a class="commit-link" href="${esc(row.artifact_url||'#')}" target="_blank" rel="noopener noreferrer">${esc((row.git_commit_sha||'').slice(0,8))}</a></td><td>${badge(row.validation_status)}</td><td>${stamp(row.validated_at||row.created_at)}</td><td><div class="row-actions">${iconAction('neutral','file-text','Notes','data-edit-notes',row.id,'Notes')}${canDeploy?iconAction('publish','send','Deploy release','data-offer-release',row.id,'Deploy release: Deploy the newest approved release'):row.status==='available'&&!isActive&&!canRollback?`<button type="button" class="table-action action-publish disabled icon-only" disabled title="Only the newest approved release can be deployed" aria-label="Not deployable">${icon('lock')}</button>`:''}${canRollback?iconAction('rollback','rotate-ccw','Roll back','data-rollback-release',row.id,`Roll back to this: Restore the immediately previous verified version for ${rollbackClients.length} eligible client(s)`):''}</div></td></tr>`;
  }).join('')}</tbody></table></div>`;
}
function renderClients(){const actions=`<div class="row-actions"><div class="view-toggle"><button data-client-view="grid" class="${state.clientView==='grid'?'selected':''}">Cards</button><button data-client-view="table" class="${state.clientView==='table'?'selected':''}">Table</button></div><button class="secondary view-action" data-open-client>${icon('plus')} Register client</button></div>`;return `${viewTitle('Client environments','Operational targets remain separate from their deployment history',actions)}${panel('Environment registry','Installed versions, database providers, connector health, and lifecycle controls',clientTable(state.clients))}`}
function clientActions(row){return `<div class="row-actions client-controls">${action('neutral','settings','Settings','data-configure-client',row.id,'Edit client settings')}${action('reject','trash-2','Delete','data-delete-client',row.id,'Delete client and associated configuration')}</div>`}
function clientTable(rows){if(!rows.length)return empty('No client environments registered.',`<button class="text-action" data-open-deployment>Register one in a deployment</button>`);if(state.clientView==='table')return `<div class="table-wrap"><table><thead><tr><th>Client</th><th>Environment</th><th>Database</th><th>Health</th><th>Services</th><th>Admin</th><th></th></tr></thead><tbody>${rows.map(row=>`<tr><td><div class="client-name"><span class="client-logo">${esc(row.name[0])}</span><div><strong>${esc(row.name)}</strong><small>${esc(row.code)}</small></div></div></td><td>${badge(row.environment)}</td><td>${esc(row.database_provider)}</td><td><span class="health ${esc(row.last_health_status)}"><i></i>${esc(row.last_health_status)}</span></td><td>${esc(row.installed_services_version||'Not reported')}</td><td>${esc(row.installed_admin_version||'Not reported')}</td><td>${clientActions(row)}</td></tr>`).join('')}</tbody></table></div>`;return `<div class="client-card-grid">${rows.map(row=>`<article class="client-card"><div class="client-card-top"><span class="client-logo large">${esc(row.name[0])}</span><div><h3>${esc(row.name)}</h3><p>${esc(row.code)}</p></div>${badge(row.environment)}</div><div class="client-health"><span class="health ${esc(row.last_health_status)}"><i></i>${esc(row.last_health_status)}</span><small>${esc(row.database_provider)}</small></div><dl><div><dt>Reporting Services</dt><dd>${esc(row.installed_services_version||'Not reported')}</dd></div><div><dt>Administration</dt><dd>${esc(row.installed_admin_version||'Not reported')}</dd></div></dl>${clientActions(row)}</article>`).join('')}</div>`}

function renderTickets(){return `${viewTitle('Support ticket manager','Only issues approved by a client administrator are synchronized here')}${panel('Escalated tickets','Sanitized diagnostics and delivery lifecycle',ticketTable(state.tickets,false))}`}
function ticketTable(rows,compact){if(!rows.length)return empty('No client-approved tickets.');return `<div class="table-wrap"><table><thead><tr><th>Ticket</th><th>Client</th><th>Issue</th><th>Severity</th><th>Status</th>${compact?'':'<th>Approved by</th><th>Update</th>'}</tr></thead><tbody>${rows.map(row=>`<tr><td><strong>${esc(row.external_number)}</strong><small>${stamp(row.created_at)}</small></td><td>${esc(row.client_name)}</td><td><strong>${esc(row.title)}</strong><small>${esc(row.component)}</small></td><td>${badge(row.severity)}</td><td>${badge(row.status)}</td>${compact?'':`<td>${esc(row.approved_by)}</td><td><select class="ticket-status compact-select" data-ticket="${row.id}">${['escalated','triaged','in_progress','fix_available','deployed','closed'].map(value=>`<option ${row.status===value?'selected':''}>${value}</option>`).join('')}</select></td>`}</tr>`).join('')}</tbody></table></div>`}

function renderSettings(){
  const theme=document.documentElement.dataset.theme;const github=state.settings.github||{};const repositories=state.settings.repositories||{};const server=state.settings.server||{};const supabase=state.settings.storageBackend==='supabase-postgresql';
  return `${viewTitle('Manager settings','Configure this console without affecting client report environments')}<div class="settings-grid">
    <article class="settings-card"><div class="settings-icon">${icon('sun')}</div><div><h3>Appearance</h3><p>Choose the interface theme for this device.</p><div class="theme-options"><button data-theme-choice="light" class="${theme==='light'?'selected':''}">${icon('sun')} Light</button><button data-theme-choice="dark" class="${theme==='dark'?'selected':''}">${icon('moon')} Dark</button></div></div></article>
    <article class="settings-card source-setting"><div class="settings-icon purple">${icon('box')}</div><div><h3>GitHub release source</h3>${github.locked?`<p class="locked-source">${esc(github.repository)}</p><div class="locked-actions"><span class="protected">Locked</span><button type="button" class="action-btn-small" data-unlock-github>Edit source</button></div>`:`<p>Enter the code repository. It is automatically used for release discovery.</p><form id="githubSettingsForm"><input name="repository" type="url" required value="${esc(github.repository||'')}" placeholder="https://github.com/organisation/repository"><div class="form-actions"><button class="primary" type="submit">Save & lock</button>${github.repository?`<button class="secondary" type="button" data-cancel-github>Cancel</button>`:''}</div></form>`}</div></article>
    <article class="settings-card source-setting"><div class="settings-icon purple">${icon('grid')}</div><div><h3>Application repositories</h3>${repositories.locked?`<p class="locked-source"><b>Services</b><br>${esc(repositories.services_repository)}<br><br><b>Administration</b><br>${esc(repositories.admin_repository)}</p><div class="locked-actions"><span class="protected">Deployment source locked</span><button type="button" class="action-btn-small" data-unlock-repositories>Edit repositories</button></div>`:`<p>Set the two deployment repositories. The wizard will reuse these references.</p><form id="repositorySettingsForm" class="repository-form"><label class="form-label"><span>Reporting Services</span><input name="services_repository" type="url" required value="${esc(repositories.services_repository||'')}" placeholder="Reporting Services GitHub URL"></label><label class="form-label"><span>Administration</span><input name="admin_repository" type="url" required value="${esc(repositories.admin_repository||'')}" placeholder="Admin Console GitHub URL"></label><div class="form-actions"><button class="primary" type="submit">Save & lock</button>${repositories.services_repository?`<button class="secondary" type="button" data-cancel-repositories>Cancel</button>`:''}</div></form>`}</div></article>
    <article class="settings-card source-setting"><div class="settings-icon purple">${icon('database')}</div><div><h3>Server & DigitalOcean Target</h3>${server.locked?`<p class="locked-source"><b>Host IP:</b> ${esc(server.host||'Not configured')}<br><b>SSH:</b> ${esc(server.ssh_user||'root')}@${esc(server.host||'')}:${server.ssh_port||22}<br><b>Services Dir:</b> ${esc(server.services_target_dir)}<br><b>Admin Dir:</b> ${esc(server.admin_target_dir)}</p><div class="locked-actions"><span class="protected">Server locked</span><button type="button" class="action-btn-small" data-unlock-server>Edit target</button><button type="button" class="action-btn-small" data-test-server-settings>Test connection</button></div>`:`<p>Configure the default target server IP and credentials used for DigitalOcean and SSH deployments.</p><form id="serverSettingsForm" class="repository-form"><label class="form-label"><span>Server Host / IP</span><input name="host" value="${esc(server.host||'139.59.32.39')}" placeholder="139.59.32.39"></label><div style="display:grid;grid-template-columns:1fr 1fr;gap:8px"><label class="form-label"><span>SSH User</span><input name="ssh_user" value="${esc(server.ssh_user||'root')}"></label><label class="form-label"><span>SSH Port</span><input name="ssh_port" type="number" value="${server.ssh_port||22}"></label></div><label class="form-label"><span>Services Target Dir</span><input name="services_target_dir" value="${esc(server.services_target_dir||'/var/www/reportingtool-webservice')}"></label><label class="form-label"><span>Admin Target Dir</span><input name="admin_target_dir" value="${esc(server.admin_target_dir||'/var/www/reportingtool-admin')}"></label><label class="form-label"><span>DigitalOcean API Token (optional)</span><input name="digitalocean_token" type="password" value="${esc(server.digitalocean_token||'')}" placeholder="dop_v1_..."></label><div class="form-actions"><button class="primary" type="submit">Save & lock</button><button class="secondary" type="button" data-test-server-settings>Test connection</button>${server.host?`<button class="secondary" type="button" data-cancel-server>Cancel</button>`:''}</div></form>`}<div id="serverSettingsTestResult" style="margin-top:8px;font-size:12px"></div></div></article>
    <article class="settings-card"><div class="settings-icon green">${icon('database')}</div><div><h3>Dedicated manager source</h3><p>${supabase?'Connected to the configured Supabase PostgreSQL source.':'Local SQLite is active. Add VTAB_MANAGER_DATABASE_URL to .env to use the dedicated Supabase source.'}</p><span class="${supabase?'protected':'planned'}">${supabase?'Supabase connected':'Setup required'}</span></div></article>
    <article class="settings-card"><div class="settings-icon pink">${icon('key')}</div><div><h3>Operator authentication</h3><p>A dedicated sign-in screen and identity provider integration are reserved for the next security phase.</p><span class="planned">Planned</span></div></article>
  </div>${panel('Audit trail','Release, deployment, ticket, connector, and configuration events',auditTable(state.audit),'audit-panel','<button class="secondary view-action" data-export-audit>Export CSV</button>')}`;
}
function auditTable(rows){if(!rows.length)return empty('No audit activity yet.');return `<div class="table-wrap"><table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Object</th><th>Details</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${stamp(row.created_at)}</td><td>${esc(row.actor)}</td><td><strong>${esc(row.action)}</strong></td><td>${esc(row.object_type)} ${esc(row.object_id||'')}</td><td class="audit-details" title="${esc(JSON.stringify(row.details||{}))}">${esc(JSON.stringify(row.details||{}))}</td></tr>`).join('')}</tbody></table></div>`}

function bindActions(){
  document.querySelectorAll('[data-open-deployment]').forEach(button=>button.onclick=openDeployment);
  document.querySelectorAll('[data-open-client]').forEach(button=>button.onclick=()=>openDialog('clientDialog'));
  document.querySelectorAll('[data-open-release]').forEach(button=>button.onclick=openReleaseDialog);
  document.querySelectorAll('[data-publish]').forEach(button=>button.onclick=()=>publishRelease(button.dataset.publish));
  document.querySelectorAll('[data-validate-release]').forEach(button=>button.onclick=()=>validateRelease(button.dataset.validateRelease));
  document.querySelectorAll('[data-edit-notes]').forEach(button=>button.onclick=()=>editReleaseNotes(button.dataset.editNotes));
  document.querySelectorAll('[data-offer-release]').forEach(button=>button.onclick=()=>offerRelease(button.dataset.offerRelease));
  document.querySelectorAll('[data-edit-version]').forEach(button=>button.onclick=()=>assignReleaseVersion(button.dataset.editVersion));
  document.querySelectorAll('[data-approve-release]').forEach(button=>button.onclick=()=>reviewRelease(button.dataset.approveRelease,'approve'));
  document.querySelectorAll('[data-reject-release]').forEach(button=>button.onclick=()=>reviewRelease(button.dataset.rejectRelease,'reject'));
  document.querySelectorAll('[data-rollback-release]').forEach(button=>button.onclick=()=>rollbackRelease(button.dataset.rollbackRelease));
  document.querySelectorAll('[data-sync-github]').forEach(button=>button.onclick=syncGitHubCandidates);
  document.querySelectorAll('[data-configure-client]').forEach(button=>button.onclick=()=>openConfiguration(button.dataset.configureClient));
  document.querySelectorAll('[data-delete-client]').forEach(button=>button.onclick=()=>deleteClient(button.dataset.deleteClient));
  document.querySelectorAll('[data-client-view]').forEach(button=>button.onclick=()=>{state.clientView=button.dataset.clientView;localStorage.setItem('vtab_client_view',state.clientView);render()});
  document.querySelectorAll('[data-toggle-deployment-history]').forEach(button=>button.onclick=()=>{state.deploymentHistoryCollapsed=!state.deploymentHistoryCollapsed;localStorage.setItem('vtab_deployment_history_collapsed',String(state.deploymentHistoryCollapsed));render()});
  document.querySelectorAll('[data-export-audit]').forEach(button=>button.onclick=exportAuditCsv);
  document.querySelectorAll('[data-approve]').forEach(button=>button.onclick=()=>approveDeployment(button.dataset.approve));
  document.querySelectorAll('[data-reject-deployment]').forEach(button=>button.onclick=()=>rejectDeployment(button.dataset.rejectDeployment));
  const insertEnvBtn=$('insertConnectorVars');if(insertEnvBtn)insertEnvBtn.onclick=insertConnectorVariables;
  document.querySelectorAll('.ticket-status').forEach(select=>select.onchange=()=>updateTicket(select.dataset.ticket,select.value));
  document.querySelectorAll('[data-theme-choice]').forEach(button=>button.onclick=()=>applyTheme(button.dataset.themeChoice));
  document.querySelectorAll('[data-close-dialog]').forEach(button=>button.onclick=()=>button.closest('dialog').close());
  document.querySelectorAll('[data-go-settings]').forEach(button=>button.onclick=()=>setView('settings'));
  document.querySelectorAll('[data-refresh-github]').forEach(button=>button.onclick=syncGitHubCandidates);
  document.querySelectorAll('[data-unlock-repositories]').forEach(button=>button.onclick=unlockRepositories);
  document.querySelectorAll('[data-cancel-repositories]').forEach(button=>button.onclick=cancelRepositories);
  document.querySelectorAll('[data-unlock-github]').forEach(button=>button.onclick=unlockGitHub);
  document.querySelectorAll('[data-cancel-github]').forEach(button=>button.onclick=cancelGitHub);
  document.querySelectorAll('[data-unlock-server]').forEach(button=>button.onclick=unlockServer);
  document.querySelectorAll('[data-cancel-server]').forEach(button=>button.onclick=cancelServer);
  document.querySelectorAll('[data-test-server-settings]').forEach(button=>button.onclick=()=>{
    const f=$('serverSettingsForm');
    const host=f?.host?.value||state.settings?.server?.host||'139.59.32.39';
    const port=Number(f?.ssh_port?.value||state.settings?.server?.ssh_port||22);
    const user=f?.ssh_user?.value||state.settings?.server?.ssh_user||'root';
    runConnectionTest({provider:'digitalocean',host,ssh_port:port,ssh_user:user}, $('serverSettingsTestResult'));
  });
  const githubForm=$('githubSettingsForm');if(githubForm)githubForm.onsubmit=saveGitHubSettings;
  const repositoryForm=$('repositorySettingsForm');if(repositoryForm)repositoryForm.onsubmit=saveRepositorySettings;
  const serverForm=$('serverSettingsForm');if(serverForm)serverForm.onsubmit=saveServerSettings;
  const cfgTestBtn=$('configTestConn');if(cfgTestBtn)cfgTestBtn.onclick=()=>{
    const f=$('configurationForm');
    const host=f.server_host?.value||'';
    const port=Number(f.ssh_port?.value||22);
    const user=f.ssh_user?.value||'root';
    runConnectionTest({provider:'digitalocean',host,ssh_port:port,ssh_user:user}, $('configTestConnResult'));
  };
  const wizTestBtn=$('wizardTestConn');if(wizTestBtn)wizTestBtn.onclick=()=>{
    const f=$('deploymentForm');
    const host=f.host?.value||state.settings?.server?.host||'139.59.32.39';
    const port=Number(f.ssh_port?.value||22);
    const user=f.ssh_user?.value||'root';
    runConnectionTest({provider:'digitalocean',host,ssh_port:port,ssh_user:user}, $('wizardTestConnResult'));
  };
}
async function unlockServer(){
  try{const result=await api('/api/v1/operator/settings/server/unlock',{method:'POST'});state.settings.server=result.server;notify('Server target settings unlocked for editing.');render()}catch(error){notify(error.message,true)}
}
async function cancelServer(){
  try{const result=await api('/api/v1/operator/settings/server/lock',{method:'POST'});state.settings.server=result.server;render()}catch(error){notify(error.message,true)}
}
async function saveServerSettings(event){
  event.preventDefault();const button=event.submitter;setBusy(button,true,'Locking…');
  try{
    const data=formData(event.currentTarget);
    const result=await api('/api/v1/operator/settings/server',{method:'PUT',body:JSON.stringify({
      host:data.host||'',
      ssh_port:Number(data.ssh_port||22),
      ssh_user:data.ssh_user||'root',
      services_target_dir:data.services_target_dir||'/var/www/reportingtool-webservice',
      admin_target_dir:data.admin_target_dir||'/var/www/reportingtool-admin',
      digitalocean_token:data.digitalocean_token||''
    })});
    state.settings.server=result.server;
    notify('Server target settings saved and locked.');
    render();
  }catch(error){notify(error.message,true)}finally{setBusy(button,false)}
}
async function runConnectionTest(params, resultEl){
  if(resultEl) resultEl.innerHTML='<span style="color:#60a5fa">Testing SSH connection to server…</span>';
  try{
    const res=await api('/api/v1/operator/test-connection',{method:'POST',body:JSON.stringify(params)});
    if(res.success){
      if(resultEl) resultEl.innerHTML=`<span style="color:#34d399;font-weight:600">✓ ${esc(res.message)}</span>`;
      notify(res.message);
    }else{
      if(resultEl) resultEl.innerHTML=`<span style="color:#f87171;font-weight:600">✕ ${esc(res.message||res.error)}</span>`;
      notify(res.message||res.error,true);
    }
  }catch(err){
    if(resultEl) resultEl.innerHTML=`<span style="color:#f87171;font-weight:600">✕ ${esc(err.message)}</span>`;
    notify(err.message,true);
  }
}
function openDialog(id){$(id).showModal()}
function releaseRepository(component){const repositories=state.settings.repositories||{};if(component==='services')return repositories.services_repository||'';if(component==='administration')return repositories.admin_repository||'';return state.settings.github?.repository||''}
function syncReleaseRepository(){$('releaseRepository').value=releaseRepository($('releaseComponent').value)}
function openReleaseDialog(){syncReleaseRepository();openDialog('releaseDialog')}
async function loadGitHubReleases(announce=true){
  try{const result=await api('/api/v1/operator/settings/github/releases');state.githubReleases=result.releases||[];if(['releases','settings'].includes(state.view))render();if(announce)notify('GitHub releases refreshed.')}catch(error){if(announce)notify(error.message,true)}
}
async function unlockGitHub(){
  try{const result=await api('/api/v1/operator/settings/github/unlock',{method:'POST'});state.settings.github=result.github;notify('GitHub release source unlocked for editing.');render()}catch(error){notify(error.message,true)}
}
async function cancelGitHub(){
  try{const result=await api('/api/v1/operator/settings/github/lock',{method:'POST'});state.settings.github=result.github;render()}catch(error){notify(error.message,true)}
}
async function saveGitHubSettings(event){
  event.preventDefault();const button=event.submitter;setBusy(button,true,'Locking…');
  try{const data=formData(event.currentTarget);const result=await api('/api/v1/operator/settings/github',{method:'PUT',body:JSON.stringify(data)});state.settings.github=result.github;notify('GitHub release source saved and locked.');render();loadGitHubReleases(false)}catch(error){notify(error.message,true)}finally{setBusy(button,false)}
}
async function unlockRepositories(){
  try{const result=await api('/api/v1/operator/settings/repositories/unlock',{method:'POST'});state.settings.repositories=result.repositories;notify('Application repositories unlocked for editing.');render()}catch(error){notify(error.message,true)}
}
async function cancelRepositories(){
  try{const result=await api('/api/v1/operator/settings/repositories/lock',{method:'POST'});state.settings.repositories=result.repositories;render()}catch(error){notify(error.message,true)}
}
async function saveRepositorySettings(event){
  event.preventDefault();const button=event.submitter;setBusy(button,true,'Locking…');
  try{const data=formData(event.currentTarget);const result=await api('/api/v1/operator/settings/repositories',{method:'PUT',body:JSON.stringify(data)});state.settings.repositories=result.repositories;notify('Application repositories saved and locked.');render()}catch(error){notify(error.message,true)}finally{setBusy(button,false)}
}
let activeConfiguration=null;
async function openConfiguration(clientId){
  try{const result=await api(`/api/v1/operator/clients/${clientId}/configuration`);if(!result.infrastructure.length)throw new Error('This client has no managed infrastructure yet. Complete the initial deployment first.');activeConfiguration=result;const form=$('configurationForm');form.reset();form.client_id.value=clientId;$('configurationTitle').textContent=`Edit ${result.client.name}`;$('configurationInfrastructure').innerHTML=result.infrastructure.map(item=>{
    const srv = item.provider_resource_id ? ` [${item.provider_resource_id}]` : '';
    return `<option value="${item.id}">${esc(item.component)} · ${esc(item.provider)}${srv} · v${item.config_version}</option>`;
  }).join('');$('configurationInfrastructure').onchange=fillConfiguration;fillConfiguration();$('configurationHistory').innerHTML=`<h3>Recent configuration versions</h3>${result.versions.slice(0,8).map(item=>`<div><b>v${item.version}</b><span>${esc(item.sync_status)} · ${esc(item.changed_by)} · ${stamp(item.created_at)}</span></div>`).join('')}`;openDialog('configurationDialog')}catch(error){notify(error.message,true)}
}
function fillConfiguration(){
  const form=$('configurationForm');
  const item=activeConfiguration.infrastructure.find(value=>value.id===form.infrastructure_id.value)||activeConfiguration.infrastructure[0];
  form.region.value=item.region||'';
  form.instance_size.value=item.instance_size||'';
  form.domain.value=item.domain||'';
  form.provider_config_json.value=JSON.stringify(item.config||{},null,2);
  const isRender = item.provider==='render'||(item.provider_resource_id&&item.provider_resource_id.startsWith('srv-'));
  const isDO = item.provider==='digitalocean';
  const renderRow = $('renderServiceIdRow');
  if(renderRow){
    renderRow.style.display = isRender ? '' : 'none';
  }
  const doRow = $('doServerRow');
  if(doRow){
    doRow.style.display = isDO ? 'block' : 'none';
  }
  const srvId = item.provider_resource_id||item.config?.service_id||'';
  if(form.render_service_id){
    form.render_service_id.value = srvId;
  }
  if(form.server_host){
    form.server_host.value = item.config?.host || item.config?.server_ip || state.settings?.server?.host || '139.59.32.39';
  }
  if(form.ssh_user){
    form.ssh_user.value = item.config?.ssh_user || state.settings?.server?.ssh_user || 'root';
  }
  if(form.ssh_port){
    form.ssh_port.value = item.config?.ssh_port || state.settings?.server?.ssh_port || 22;
  }
  if(form.target_dir){
    const defaultDir = item.component === 'services' ? (state.settings?.server?.services_target_dir || '/var/www/reportingtool-webservice') : (state.settings?.server?.admin_target_dir || '/var/www/reportingtool-admin');
    form.target_dir.value = item.config?.target_dir || defaultDir;
  }
  if(form.container_name){
    form.container_name.value = item.config?.container_name || (item.component === 'services' ? 'vtab-reporting-api' : 'reportingtool-admin');
  }
  const resEl = $('configTestConnResult');
  if(resEl) resEl.innerHTML = '';
  const env={...(item.environment_variables||{})};
  if(!env.VTAB_CLIENT_ID&&activeConfiguration?.client?.id)env.VTAB_CLIENT_ID=activeConfiguration.client.id;
  if(!env.VTAB_SIMULATE_BUILD)env.VTAB_SIMULATE_BUILD='0';
  if(!env.VTAB_SMTP_FROM)env.VTAB_SMTP_FROM='aivtabsquare@gmail.com';
  if(!env.ADMIN_OTP_ENABLED)env.ADMIN_OTP_ENABLED='1';
  if(!env.ADMIN_EMAIL)env.ADMIN_EMAIL='harishkadhi18022001@gmail.com';
  if(isRender&&srvId&&!env.RENDER_SERVICE_ID)env.RENDER_SERVICE_ID=srvId;
  form.environment_variables_json.value=JSON.stringify(env,null,2);
  if(form.sync_provider) form.sync_provider.checked = true;
  if(form.provider_token && env.RENDER_API_KEY && !env.RENDER_API_KEY.startsWith('rnd_...') && !form.provider_token.value){
    form.provider_token.value = env.RENDER_API_KEY;
  }
}
function insertConnectorVariables(){
  const form=$('configurationForm');
  try{
    const env=JSON.parse(form.environment_variables_json.value||'{}');
    const clientId=form.client_id.value||activeConfiguration?.client?.id||'';
    const item=activeConfiguration?.infrastructure?.find(value=>value.id===form.infrastructure_id.value)||activeConfiguration?.infrastructure?.[0];
    if(!env.VTAB_CLIENT_ID&&clientId)env.VTAB_CLIENT_ID=clientId;
    if(!env.VTAB_AGENT_TOKEN)env.VTAB_AGENT_TOKEN='        ';
    if(!env.VTAB_SIMULATE_BUILD)env.VTAB_SIMULATE_BUILD='0';
    if(item?.provider==='render'||item?.provider_resource_id?.startsWith('srv-')){
      const srvId=form.render_service_id?.value||item?.provider_resource_id||item?.config?.service_id||'';
      if(srvId)env.RENDER_SERVICE_ID=srvId;
      if(!env.RENDER_API_KEY)env.RENDER_API_KEY='rnd_...';
    }
    form.environment_variables_json.value=JSON.stringify(env,null,2);
    notify('Connector and Render variables inserted.');
  }catch(e){
    notify('Invalid JSON in environment variables.',true);
  }
}
async function rejectDeployment(deploymentId){
  const deployment=state.deployments.find(d=>d.id===deploymentId);
  if(!deployment||!confirm(`Reject deployment of ${componentLabel(deployment.component)} ${versionLabel(deploymentVersion(deployment))} to ${deployment.client_name}? This offer will not be sent to the client.`))return;
  try{await api(`/api/v1/operator/deployments/${deploymentId}/reject`,{method:'POST'});notify('Deployment rejected. No rollout was initiated.');await loadAll()}catch(error){notify(error.message,true)}
}
async function saveConfiguration(event){event.preventDefault();const form=event.currentTarget;const button=event.submitter;setBusy(button,true,'Saving…');try{const provider_config=JSON.parse(form.provider_config_json.value||'{}');Object.assign(provider_config,{region:form.region.value,instance_size:form.instance_size.value,domain:form.domain.value});if(form.render_service_id&&form.render_service_id.value.trim()){provider_config.service_id=form.render_service_id.value.trim();}if(form.server_host&&form.server_host.value.trim()){provider_config.host=form.server_host.value.trim();provider_config.ssh_user=(form.ssh_user?.value||'root').trim();provider_config.ssh_port=Number(form.ssh_port?.value||22);if(form.target_dir?.value)provider_config.target_dir=form.target_dir.value.trim();if(form.container_name?.value)provider_config.container_name=form.container_name.value.trim();}const environment_variables=JSON.parse(form.environment_variables_json.value||'{}');await api(`/api/v1/operator/clients/${form.client_id.value}/configuration`,{method:'PUT',body:JSON.stringify({infrastructure_id:form.infrastructure_id.value,provider_config,environment_variables,change_summary:form.change_summary.value,sync_provider:form.sync_provider.checked,provider_token:form.provider_token.value||environment_variables.RENDER_API_KEY||null})});form.provider_token.value='';form.closest('dialog').close();notify('Configuration saved as a new audited version.');await loadAll()}catch(error){notify(error.message,true)}finally{setBusy(button,false)}}
async function validateRelease(id){try{await api(`/api/v1/operator/releases/${id}/validate`,{method:'POST',body:JSON.stringify({approved:true,ci_status:'success',checks:{build:'success',test:'success',security:'success'},notes:'Approved for deployment'})});notify('Release is now approved and available.');await loadAll()}catch(error){notify(error.message,true)}}
async function editReleaseNotes(id){const row=state.releases.find(item=>item.id===id);const release_notes=prompt('Release notes:',row?.release_notes||'');if(release_notes===null)return;try{await api(`/api/v1/operator/releases/${id}/notes`,{method:'PUT',body:JSON.stringify({release_notes})});notify('Release notes updated.');await loadAll()}catch(error){notify(error.message,true)}}
async function syncGitHubCandidates(){
  try {
    notify('Syncing latest commits from GitHub for Services & Administration...');
    const results = await Promise.allSettled([
      api('/api/v1/operator/releases/sync-github', {method: 'POST', body: JSON.stringify({component: 'services', branch: 'main', limit: 10})}),
      api('/api/v1/operator/releases/sync-github', {method: 'POST', body: JSON.stringify({component: 'administration', branch: 'main', limit: 10})})
    ]);
    let count = 0;
    for (const r of results) {
      if (r.status === 'fulfilled' && r.value && r.value.releases) count += r.value.releases.length;
    }
    notify(`${count} commit candidates synchronized across Services & Administration.`);
    await loadAll();

  } catch(error) {
    notify(error.message, true);
  }
}
async function assignReleaseVersion(releaseId){
  const row=state.releases.find(r=>r.id===releaseId);
  if(!row||row.status!=='pending_approval')return notify('Only pending commits may be versioned.',true);
  const input=prompt('Enter the actual application version from your build or release manifest (for example, 1.8):',row.version.startsWith('commit-')?'':row.version);
  if(input===null)return;
  const version=input.trim();
  if(!/^v?\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9.-]+)?$/.test(version))return notify('Enter a valid version such as 1.8, 1.8.0 or 1.8.0-rc1.',true);
  try{await api(`/api/v1/operator/releases/${releaseId}/version`,{method:'PATCH',body:JSON.stringify({version})});notify(`Application version ${versionLabel(version)} saved. You can now approve this commit.`);await loadAll()}catch(error){notify(error.message,true)}
}
async function reviewRelease(releaseId,decision){
  const verb=decision==='approve'?'approve':'reject';
  if(!confirm(`${verb[0].toUpperCase()+verb.slice(1)} this incoming commit?`))return;
  try{await api(`/api/v1/operator/releases/${releaseId}/${verb}`,{method:'POST'});notify(verb==='approve'?'Version approved and added to Releases.':'Incoming version rejected.');await loadAll()}catch(error){notify(error.message,true)}
}
async function deleteClient(clientId){
  const client=state.clients.find(row=>row.id===clientId);if(!client)return;
  if(!confirm(`Delete ${client.name}? Its deployment plans, connector registration, and stored configuration history will also be removed.`))return;
  try{await api(`/api/v1/operator/clients/${clientId}`,{method:'DELETE'});notify(`${client.name} was deleted.`);await loadAll()}catch(error){notify(error.message,true)}
}
function csvCell(value){const text=typeof value==='string'?value:JSON.stringify(value??'');return `"${String(text).replaceAll('"','""')}"`}
function exportAuditCsv(){
  if(!state.audit.length)return notify('No audit trail records to export.',true);
  const headers=['created_at','actor','action','object_type','object_id','client_id','details'];
  const lines=[headers.map(csvCell).join(','),...state.audit.map(row=>headers.map(key=>csvCell(row[key])).join(','))];
  const blob=new Blob(['\ufeff'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'});const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=`vtab_manager_audit_trail_${new Date().toISOString().slice(0,10)}.csv`;document.body.appendChild(link);link.click();link.remove();URL.revokeObjectURL(url);notify('Audit trail exported successfully.');
}
function rollbackRelease(releaseId){offerRelease(releaseId,true)}
function offerCandidates(){return state.offerMode==='rollback'?state.clients.filter(client=>state.offerAllowedClientIds.has(client.id)):state.clients}
function renderOfferClientList(){
  const list=$('offerClientList');if(!list)return;
  const query=state.offerSearch.trim().toLowerCase();const rows=offerCandidates().filter(client=>!query||`${client.name} ${client.code} ${client.environment}`.toLowerCase().includes(query));
  list.innerHTML=rows.length?rows.map(client=>`<label class="offer-client"><input type="checkbox" value="${esc(client.id)}" ${state.offerClientIds.has(client.id)?'checked':''}><span class="client-logo">${esc(client.name[0])}</span><span><b>${esc(client.name)}</b><small>${esc(client.code)} · ${esc(client.environment)}</small></span></label>`).join(''):empty(state.offerMode==='rollback'?'No eligible clients for this one-step rollback.':'No matching clients.');
  list.querySelectorAll('input[type="checkbox"]').forEach(input=>input.onchange=()=>{input.checked?state.offerClientIds.add(input.value):state.offerClientIds.delete(input.value);updateOfferSelectionCount()});
  updateOfferSelectionCount();
}
function updateOfferSelectionCount(){const node=$('offerSelectionCount');if(node)node.textContent=`${state.offerClientIds.size} client${state.offerClientIds.size===1?'':'s'} selected`}
function offerRelease(releaseId,rollback=false){
  const release=state.releases.find(r=>r.id===releaseId);
  if(!release||release.validation_status!=='approved'||!['available','superseded'].includes(release.status))return notify('Release is not approved or deployable.',true);
  state.offerMode=rollback?'rollback':'update';const fallbackClients=rollbackClientIds(release,liveReleaseIds());const allowed=(release.rollback_eligible_client_ids&&release.rollback_eligible_client_ids.length)?release.rollback_eligible_client_ids:fallbackClients;state.offerAllowedClientIds=new Set(rollback?allowed:state.clients.map(c=>c.id));
  if(!state.offerAllowedClientIds.size)return notify(rollback?'No eligible clients for rollback; only the immediately previous deployed version may be restored.':'Register a client before offering an update.',true);
  state.offerClientIds=new Set(state.offerAllowedClientIds);state.offerSearch='';state.offerNonce=typeof crypto!=='undefined'&&crypto.randomUUID?crypto.randomUUID():`${Date.now()}-${Math.random()}`;
  if($('offerClientSearch'))$('offerClientSearch').value='';renderOfferClientList();
  $('offerDialogTitle').textContent=rollback?'Roll back to previous version':'Deploy approved release';
  $('offerDialogHelp').textContent=rollback?'Strict one-step rollback: only clients currently running the succeeding verified version can be selected. Approval is still required before rollout.':'Choose clients that should receive this approved release. Each client must separately approve the rollout.';
  $('offerSubmitButton').innerHTML=rollback?`${icon('rotate-ccw')} Request rollback`:`${icon('send')} Deploy to selected clients`;
  $('offerReleaseId').value=releaseId;
  $('offerVersionText').textContent=`${componentLabel(release.component)} · ${versionLabel(release.version)}`;
  $('offerCommitText').textContent=release.git_commit_sha||release.checksum_sha256?.substring(0,12)||'—';
  const search=$('offerClientSearch');if(search)search.oninput=()=>{state.offerSearch=search.value;renderOfferClientList()};
  const all=$('offerSelectAllBtn');if(all)all.onclick=()=>{const query=state.offerSearch.trim().toLowerCase();offerCandidates().filter(client=>!query||`${client.name} ${client.code} ${client.environment}`.toLowerCase().includes(query)).forEach(client=>state.offerClientIds.add(client.id));renderOfferClientList()};
  const clear=$('offerDeselectAllBtn');if(clear)clear.onclick=()=>{state.offerClientIds.clear();renderOfferClientList()};
  $('offerDialog').showModal();
}
function setClientMode(mode){
  const isNew=mode==='new';$('existingClientFields').hidden=isNew;$('newClientFields').hidden=!isNew;
  ['new_name','new_code','new_contact_email'].forEach(name=>{const field=document.querySelector(`[name="${name}"]`);field.required=isNew});
  $('deploymentClient').required=!isNew;
}
const wizardCopy=[
  ['Choose the client environment','Select a registered client or create the deployment target inline.'],
  ['Select applications and releases','Choose Reporting Services, the Admin Console, or a coordinated deployment.'],
  ['Choose the infrastructure provider','Provider adapters keep the deployment workflow consistent across platforms.'],
  ['Configure runtime and protected values','Provider credentials and application secrets are used for this request only.'],
  ['Validate every hard prerequisite','Provisioning is blocked until the complete preflight passes.'],
  ['Review and authorize provisioning','Explicit confirmation is required because this can create billable resources.'],
  ['Deployment accepted','Track execution, health verification, and rollback metadata from the manager.']
];
const executionStages=['Validate input','Verify release','Snapshot rollback state','Provision infrastructure','Configure runtime','Deploy artifact','Run migrations','Check health','Activate connector','Finalize audit'];
function setWizardStep(step){
  state.wizardStep=Math.max(1,Math.min(7,step));
  document.querySelectorAll('[data-wizard-step]').forEach(section=>section.classList.toggle('active',Number(section.dataset.wizardStep)===state.wizardStep));
  document.querySelectorAll('#wizardSteps li').forEach((item,index)=>{item.classList.toggle('active',index+1===state.wizardStep);item.classList.toggle('done',index+1<state.wizardStep)});
  const copy=wizardCopy[state.wizardStep-1];$('wizardTitle').textContent=copy[0];$('wizardSubtitle').textContent=copy[1];
  $('wizardProgressText').textContent=`Step ${state.wizardStep} of 7`;$('wizardProgressBar').style.width=`${state.wizardStep/7*100}%`;
  $('wizardBack').hidden=state.wizardStep===1||state.wizardStep===7;$('wizardNext').hidden=state.wizardStep>=6;$('wizardDeploy').hidden=state.wizardStep!==6;
}
function syncApplicationFields(){
  const checked=document.querySelector('[name="application"]:checked');
  const application=checked?checked.value:'services';
  const servicesLabel=$('wizardServicesLabel')||$('wizardServicesRelease')?.closest('label');
  const adminLabel=$('wizardAdminLabel')||$('wizardAdminRelease')?.closest('label');
  const picker=document.querySelector('.release-picker');
  if(servicesLabel){
    const showServices=application==='services'||application==='both';
    servicesLabel.hidden=!showServices;
    servicesLabel.style.display=showServices?'block':'none';
  }
  if(adminLabel){
    const showAdmin=application==='administration'||application==='both';
    adminLabel.hidden=!showAdmin;
    adminLabel.style.display=showAdmin?'block':'none';
  }
  if(picker){
    picker.style.gridTemplateColumns=application==='both'?'repeat(2, minmax(0, 1fr))':'1fr';
  }
}
function syncProviderFields(){
  const provider=document.querySelector('[name="provider"]:checked').value;
  document.querySelectorAll('[data-render-field]').forEach(node=>node.hidden=provider!=='render');
  document.querySelectorAll('[data-do-field]').forEach(node=>node.hidden=provider!=='digitalocean');
  const region=document.querySelector('[name="region"]');region.value=provider==='render'?'oregon':'blr1';
  document.querySelector('[name="instance_size"]').value=provider==='render'?'free':'s-2vcpu-4gb';
}
async function openDeployment(){
  const form=$('deploymentForm');form.reset();state.wizardStep=1;state.wizardClientId='';state.wizardBootstrapToken='';state.wizardPreflight=null;state.wizardJobs=[];
  try{
    const freshReleases=await api('/api/v1/operator/releases');
    if(freshReleases&&Array.isArray(freshReleases.releases)){
      state.releases=freshReleases.releases;
    }
  }catch(err){console.warn('Could not refresh releases catalog',err)}
  $('deploymentClient').innerHTML=state.clients.length?state.clients.map(client=>`<option value="${client.id}">${esc(client.name)} · ${esc(client.environment)}</option>`).join(''):'<option value="">No registered clients</option>';
  const releaseOptions=component=>{
    const available=state.releases
      .filter(release=>release.status==='available'&&release.component===component)
      .sort((a,b)=>new Date(b.created_at||0) - new Date(a.created_at||0));
    return available.length?available.map(release=>`<option value="${release.id}">v${esc(release.version)} · ${esc(release.channel)}</option>`).join(''):'<option value="">No published release</option>';
  };
  $('wizardServicesRelease').innerHTML=releaseOptions('services');
  $('wizardAdminRelease').innerHTML=releaseOptions('administration');
  const mode=state.clients.length?'existing':'new';document.querySelector(`[name="client_mode"][value="${mode}"]`).checked=true;setClientMode(mode);
  $('preflightScore').textContent='0%';$('preflightResults').innerHTML='<div class="preflight-empty">Run the checks after completing configuration.</div>';
  $('executionStages').innerHTML=executionStages.map((stage,index)=>`<div><span>${index+1}</span><b>${esc(stage)}</b><small>Pending</small></div>`).join('');$('deploymentJobs').innerHTML='';
  syncApplicationFields();syncProviderFields();setWizardStep(1);
  openDialog('deploymentDialog');
}
function validateWizardStep(){
  const section=document.querySelector(`[data-wizard-step="${state.wizardStep}"]`);const controls=[...section.querySelectorAll('input,select,textarea')].filter(control=>!control.closest('[hidden]')&&!control.disabled);
  for(const control of controls){if(!control.checkValidity()){control.reportValidity();return false}}
  return true;
}
async function ensureWizardClient(){
  const data=formData($('deploymentForm'));if(data.client_mode==='existing'){if(!data.client_id)throw new Error('Choose a client environment.');state.wizardClientId=data.client_id;return}
  if(state.wizardClientId)return;
  const result=await api('/api/v1/operator/clients',{method:'POST',body:JSON.stringify({name:data.new_name,code:data.new_code,contact_email:data.new_contact_email,environment:data.new_environment||'production',database_provider:data.new_database_provider||'supabase'})});
  state.wizardClientId=result.client.id;state.wizardBootstrapToken=`Connector token:\n${result.connectorBootstrapToken}\n\nClient administrator token:\n${result.clientAdminBootstrapToken}`;
}
function wizardPlan(){
  const data=formData($('deploymentForm'));const repositories=state.settings.repositories||{};const application=data.application||'services';
  const environment_variables={};['SUPABASE_URL','VITE_SUPABASE_ANON_KEY','SUPABASE_SERVICE_ROLE_KEY','VTAB_WEB_URL','BREVO_API_KEY','ADMIN_EMAIL','VTAB_SMTP_FROM','ADMIN_OTP_ENABLED'].forEach(key=>{
    const value=data[`env_${key}`];if(value)environment_variables[key]=typeof value==='string'?value.replace(/^[`'"\s]+|[`'"\s]+$/g,''):value;
  });
  if(environment_variables.SUPABASE_URL)environment_variables.VITE_SUPABASE_URL=environment_variables.SUPABASE_URL;
  const clientId=state.wizardClientId||data.client_id;
  if(clientId)environment_variables.VTAB_CLIENT_ID=clientId;
  if(state.wizardBootstrapToken&&typeof state.wizardBootstrapToken==='string'){
    const m=state.wizardBootstrapToken.match(/vtab_tok_[a-f0-9]+/);
    if(m)environment_variables.VTAB_AGENT_TOKEN=m[0];
  }
  environment_variables.VTAB_AGENT_TOKEN = environment_variables.VTAB_AGENT_TOKEN || 'SDCsfIsSnlOj4ceWprbj-0GrsjxESrOT1DnmNJOGa_Y';
  environment_variables.VTAB_MANAGER_URL = environment_variables.VTAB_MANAGER_URL || 'https://manager.139.59.32.39.nip.io';
  environment_variables.VTAB_AUTO_OFFER_GITHUB = 'false';
  environment_variables.VTAB_SIMULATE_BUILD = '0';
  environment_variables.ADMIN_OTP_ENABLED = environment_variables.ADMIN_OTP_ENABLED || '1';
  environment_variables.ADMIN_EMAIL = environment_variables.ADMIN_EMAIL || 'harishkadhi18022001@gmail.com';
  environment_variables.VTAB_SMTP_FROM = environment_variables.VTAB_SMTP_FROM || 'aivtabsquare@gmail.com';
  environment_variables.SUPABASE_URL = environment_variables.SUPABASE_URL || 'https://asjscibujiikthxmpxgg.supabase.co';
  environment_variables.VITE_SUPABASE_URL = environment_variables.SUPABASE_URL;
  if (application === 'administration' || application === 'both') {
    environment_variables.PORT = '8835';
    environment_variables.SERVICES_API_URL = environment_variables.SERVICES_API_URL || 'https://reportservices.vtabsquare.com/api/v1';
    environment_variables.VTAB_ADMIN_URL = environment_variables.VTAB_ADMIN_URL || (data.domain && !data.domain.includes('admin-reporting.vtabsquare.com') ? `https://${data.domain}` : 'https://admin-reporting.139.59.32.39.nip.io');
  }
  const release_ids={};if(application!=='administration'&&data.services_release)release_ids.services=data.services_release;if(application!=='services'&&data.administration_release)release_ids.administration=data.administration_release;
  const cleanVal=v=>typeof v==='string'?v.replace(/^[`'"\s]+|[`'"\s]+$/g,''):v;
  const targetDir=cleanVal(data.target_dir)||(application==='administration'?'/var/www/reportingtool-admin':'/var/www/reportingtool-webservice');
  const domain=cleanVal(data.domain)||(application==='administration'?'admin-reporting.139.59.32.39.nip.io':'reportservices.vtabsquare.com');
  const host=cleanVal(data.host)||state.settings?.server?.host||'139.59.32.39';
  const sshUser=cleanVal(data.ssh_user)||state.settings?.server?.ssh_user||'root';
  const sshPort=Number(cleanVal(data.ssh_port)||state.settings?.server?.ssh_port||22);
  return {client_id:clientId,application,provider:data.provider||'digitalocean',release_ids,repositories:{services:repositories.services_repository||'',administration:repositories.admin_repository||''},provider_config:{service_name:cleanVal(data.service_name)||(application==='administration'?'reportingtool-admin':'vtab-reporting-v2'),region:data.region||'blr1',instance_size:data.instance_size||'s-2vcpu-4gb',owner_id:data.owner_id||'',service_id:data.service_id||'',branch:data.branch||'main',image:data.image||'ubuntu-24-04-x64',domain:domain,ssl:Boolean(domain&&domain.trim()),host:host,ssh_user:sshUser,ssh_port:sshPort,target_dir:targetDir},environment_variables};
}
function renderPreflight(result){
  const checks=result.checks||[];const passed=checks.filter(check=>check.status==='passed').length;const score=checks.length?Math.round(passed/checks.length*100):0;
  $('preflightScore').textContent=`${score}%`;$('preflightScore').classList.toggle('approved',result.approved);
  $('preflightResults').innerHTML=checks.map(check=>`<article class="preflight-check ${esc(check.status)}"><span>${check.status==='passed'?icon('check'):icon('shield')}</span><div><b>${esc(check.label)}</b><small>${esc(check.category)}</small><p>${esc(check.message)}</p>${check.resolution?`<em>${esc(check.resolution)}</em>`:''}</div></article>`).join('');
}
async function runWizardPreflight(){
  const button=$('runPreflight');setBusy(button,true,'Validating…');
  try{await ensureWizardClient();const data=formData($('deploymentForm'));const payload={...wizardPlan(),provider_token:data.provider_token||'',verify_provider:true};const result=await api('/api/v1/operator/wizard/preflight',{method:'POST',body:JSON.stringify(payload)});state.wizardPreflight=result;renderPreflight(result);notify(result.approved?'Preflight approved.':'Preflight found blocking items.',!result.approved)}catch(error){state.wizardPreflight=null;notify(error.message,true)}finally{setBusy(button,false)}
}
async function wizardContinue(){
  const button=$('wizardNext');if(!validateWizardStep())return;setBusy(button,true,'Checking…');
  try{if(state.wizardStep===1)await ensureWizardClient();if(state.wizardStep===4)state.wizardPreflight=null;if(state.wizardStep===5&&!state.wizardPreflight?.approved)throw new Error('Run preflight and resolve every blocking check before continuing.');setWizardStep(state.wizardStep+1)}catch(error){notify(error.message,true)}finally{setBusy(button,false)}
}
async function publishRelease(id){try{await api(`/api/v1/operator/releases/${id}/publish`,{method:'POST'});notify('Release published and ready for deployment.');await loadAll()}catch(error){notify(error.message,true)}}
async function approveDeployment(id){const approved_by=prompt('Client administrator who approved this deployment:');if(!approved_by)return;try{await api(`/api/v1/operator/deployments/${id}/approve`,{method:'POST',body:JSON.stringify({approved_by})});notify('Deployment approved and available to the connector.');await loadAll()}catch(error){notify(error.message,true)}}
async function updateTicket(id,status){try{await api(`/api/v1/operator/tickets/${id}`,{method:'PATCH',body:JSON.stringify({status})});notify('Ticket status updated.');await loadAll()}catch(error){notify(error.message,true)}}
function formData(form){return Object.fromEntries([...new FormData(form).entries()].map(([k,v])=>[k,typeof v==='string'?v.replace(/^[`'"\s]+|[`'"\s]+$/g,''):v]).filter(([,value])=>value!==''))}
function wireForm(id,handler){
  const form=$(id);
  if(!form)return;
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    if(event.submitter?.value==='cancel'){form.closest('dialog')?.close();return}
    const button=event.submitter;
    setBusy(button,true);
    try{
      await handler(formData(form));
      if(typeof form.reset==='function')form.reset();
      form.closest('dialog')?.close();
      await loadAll()
    }catch(error){
      notify(error.message,true)
    }finally{
      setBusy(button,false)
    }
  })
}
wireForm('clientForm',async data=>{const result=await api('/api/v1/operator/clients',{method:'POST',body:JSON.stringify(data)});$('bootstrapToken').textContent=`Connector token:\n${result.connectorBootstrapToken}\n\nClient administrator token:\n${result.clientAdminBootstrapToken}`;setTimeout(()=>$('tokenDialog').showModal(),100);notify('Client environment registered.')});
wireForm('releaseForm',async data=>{data.manifest={schema:'vtab-release-manifest',version:1};await api('/api/v1/operator/releases',{method:'POST',body:JSON.stringify(data)});notify('Release draft created.')});
wireForm('offerForm',async data=>{
  const releaseId=data.release_id;
  const clientIds=[...state.offerClientIds];
  if(!clientIds.length||!releaseId)throw new Error('Select at least one client and a release.');
  const failures=[];let success=0;
  for(const clientId of clientIds){try{await api('/api/v1/operator/deployments',{method:'POST',headers:{'Idempotency-Key':`${state.offerMode}:${clientId}:${releaseId}:${state.offerNonce}`},body:JSON.stringify({client_id:clientId,release_id:releaseId,deployment_type:state.offerMode})});success+=1}catch(error){const client=state.clients.find(row=>row.id===clientId);failures.push(`${client?.name||clientId}: ${error.message}`)}}
  if(failures.length)throw new Error(`${success} approval requests created. Failed: ${failures.join('; ')}`);
  notify(`${state.offerMode==='rollback'?'Rollback':'Release'} approval requested for ${success} client${success===1?'':'s'}.`);
});
$('configurationForm').addEventListener('submit',saveConfiguration);
function renderWizardStep7Jobs(){
  const container = $('deploymentJobs');
  if(!container) return;
  container.innerHTML = state.wizardJobs.map(job => {
    return `<article><span>${icon('rocket')}</span><div style="flex:1"><b>${esc(job.component)}</b><small>${esc(job.deploymentId)}</small>${job.url ? `<br><a href="${esc(job.url)}" target="_blank" style="display:inline-flex;align-items:center;gap:4px;margin-top:4px;font-size:12px;color:var(--accent,#60a5fa);font-weight:600;text-decoration:none">Open application: ${esc(job.url)} ↗</a>` : ''}</div>${badge(job.status)}</article>`;
  }).join('');
}
function trackWizardDeploymentJobs(jobs){
  renderWizardStep7Jobs();
  const pollTimer = setInterval(async ()=>{
    try {
      const res = await api('/api/v1/operator/deployments');
      state.deployments = res.deployments || [];
      let anyChanged = false;
      let allDone = true;
      let successUrl = null;
      state.wizardJobs = (state.wizardJobs||[]).map(job => {
        const d = (state.deployments||[]).find(x => x.id === job.deploymentId);
        if(!d) return job;
        const targetUrl = deploymentTargetUrl(d);
        if(d.status !== job.status || targetUrl !== job.url) anyChanged = true;
        if(!['succeeded', 'failed', 'cancelled'].includes(d.status)) allDone = false;
        if(d.status === 'succeeded' && targetUrl) successUrl = targetUrl;
        return { ...job, status: d.status, stage: d.stage, url: targetUrl };
      });
      if(anyChanged) renderWizardStep7Jobs();
      if(successUrl){
        if($('completionHead')) $('completionHead').textContent = 'Deployment complete & verified!';
        if($('completionSub')) $('completionSub').textContent = 'Your application is now live and healthy.';
        if($('completionActions')) $('completionActions').innerHTML = `<a href="${esc(successUrl)}" target="_blank" class="primary" style="display:inline-flex;align-items:center;gap:6px;padding:8px 18px;border-radius:6px;text-decoration:none;font-weight:600">${icon('rocket')} Open Application ↗</a><button type="button" class="secondary" data-close-dialog>Return to dashboard</button>`;
      }
      if(allDone){
        clearInterval(pollTimer);
        await loadAll();

      }
    }catch(err){}
  }, 2500);
}
$('deploymentForm').addEventListener('submit',async event=>{
  event.preventDefault();if(state.wizardStep!==6)return;const button=$('wizardDeploy');setBusy(button,true,'Starting…');
  try{
    const data=formData(event.currentTarget);if(!data.confirm_provisioning)throw new Error('Confirm that you authorize infrastructure provisioning.');
    const result=await api('/api/v1/operator/wizard/execute',{method:'POST',body:JSON.stringify({...wizardPlan(),provider_token:data.provider_token,confirm_provisioning:true})});
    ['provider_token','env_SUPABASE_SERVICE_ROLE_KEY','env_VITE_SUPABASE_ANON_KEY','env_BREVO_API_KEY'].forEach(name=>{const field=document.querySelector(`[name="${name}"]`);if(field)field.value=''});
    state.wizardJobs=result.jobs||[];
    setWizardStep(7);
    trackWizardDeploymentJobs(state.wizardJobs);
    notify('Deployment accepted and execution tracking started.');
    if(state.wizardBootstrapToken){$('bootstrapToken').textContent=state.wizardBootstrapToken;setTimeout(()=>$('tokenDialog').showModal(),250)}
  }catch(error){notify(error.message,true)}finally{setBusy(button,false)}
});
document.querySelectorAll('[name="client_mode"]').forEach(radio=>radio.onchange=()=>setClientMode(radio.value));
document.querySelectorAll('[name="application"]').forEach(radio=>{radio.onchange=syncApplicationFields;radio.onclick=syncApplicationFields});
document.querySelectorAll('.choice-card').forEach(card=>card.addEventListener('click',()=>setTimeout(syncApplicationFields,0)));
document.querySelectorAll('[name="provider"]').forEach(radio=>radio.onchange=syncProviderFields);
$('wizardNext').onclick=wizardContinue;$('wizardBack').onclick=()=>setWizardStep(state.wizardStep-1);$('runPreflight').onclick=runWizardPreflight;
$('releaseComponent').onchange=syncReleaseRepository;
document.querySelectorAll('.nav-item').forEach(button=>button.onclick=()=>setView(button.dataset.view));
$('themeToggle').onclick=toggleTheme;$('refreshButton').onclick=loadAll;$('mobileMenu').onclick=()=>toggleMobile(true);$('mobileShade').onclick=()=>toggleMobile(false);$('primaryAction').onclick=openDeployment;
bootstrap();
