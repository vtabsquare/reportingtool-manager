const state={view:'overview',clients:[],releases:[],deployments:[],tickets:[],audit:[],summary:{},settings:{storageBackend:'sqlite',github:{},repositories:{}},githubReleases:[],key:'',wizardStep:1,wizardClientId:'',wizardBootstrapToken:'',wizardPreflight:null,wizardJobs:[]};
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
const badge=value=>{
  const raw=String(value||'');
  const lower=raw.toLowerCase();
  const label=lower==='scheduled'?'Offered':lower==='succeeded'?'Completed':lower==='pending_approval'?'Pending approval':raw.replaceAll('_',' ');
  const cls=lower==='scheduled'?'offered':lower==='succeeded'?'succeeded':lower==='pending_approval'?'pending':lower;
  return `<span class="badge ${esc(cls)}">${esc(label)}</span>`;
};
const empty=(text,action='')=>`<div class="empty"><span>${icon('box')}</span><b>${esc(text)}</b>${action}</div>`;

async function api(path,options={}){
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-VTAB-Manager-Key':state.key,...(options.headers||{})}});
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
function panel(title,subtitle,body,extraClass=''){return `<article class="panel ${extraClass}"><div class="panel-head"><div><h2>${esc(title)}</h2><p>${esc(subtitle)}</p></div></div><div class="panel-body">${body}</div></article>`}
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
function renderDeployments(){return `${viewTitle('Deployment operations','Create plans, collect client approval, and monitor the complete rollout history')}<div class="workflow-banner"><div class="workflow-step done"><span>1</span><div><b>Choose client</b><small>Existing or new</small></div></div><i></i><div class="workflow-step"><span>2</span><div><b>Select release</b><small>Published artifacts</small></div></div><i></i><div class="workflow-step"><span>3</span><div><b>Client approval</b><small>Required gate</small></div></div><i></i><div class="workflow-step"><span>4</span><div><b>Deploy & validate</b><small>Health reported</small></div></div></div>${panel('Deployment history','Offered, active, completed, and rolled-back jobs',deploymentTable(state.deployments,false))}`}
function deploymentTable(rows,compact){if(!rows.length)return empty('No deployment jobs yet.',`<button class="text-action" data-open-deployment>Create the first deployment</button>`);return `<div class="table-wrap"><table><thead><tr><th>Client</th><th>Component</th><th>Target</th><th>Status</th>${compact?'':'<th>Approval</th><th>Created</th><th></th>'}</tr></thead><tbody>${rows.map(row=>`<tr><td><div class="client-name"><span class="client-logo">${esc((row.client_name||'C')[0])}</span><strong>${esc(row.client_name)}</strong></div></td><td>${esc(row.component)}</td><td><span class="version-chip">v${esc(row.target_version)}</span></td><td>${badge(row.status)}</td>${compact?'':`<td>${esc(row.approved_by||'Waiting')}</td><td>${stamp(row.created_at)}</td><td><div class="row-actions">${row.status==='pending_approval'?`<button class="table-action primary-small" data-approve="${row.id}">Approve</button>`:''}${['scheduled','offered','apply_requested','deploying','validating'].includes(row.status)?`<button class="table-action" data-complete-deployment="${row.id}" style="color:var(--green,#30a46c)">Complete</button>`:''}${['pending_approval','scheduled','offered','apply_requested'].includes(row.status)?`<button class="table-action" data-cancel-deployment="${row.id}" style="color:var(--red,#e5484d)">Cancel</button>`:''}</div></td>`}</tr>`).join('')}</tbody></table></div>`}

function renderReleases(){return `${viewTitle('Release catalog','Build the verified artifact library used by deployment plans',`<div class="row-actions"><button class="secondary view-action" data-sync-github>${icon('refresh')} Sync GitHub</button><button class="secondary view-action" data-open-release>${icon('plus')} Create release</button></div>`)}${renderGitHubReleaseSource()}${panel('Deployable releases','Checksums, channels, and compatibility metadata',releaseTable(state.releases))}`}
function renderGitHubReleaseSource(){
  const repos=state.settings.repositories||{};
  const srvRepo = repos.services_repository || state.settings.github?.repository || 'Not configured';
  const admRepo = repos.admin_repository || 'Not configured';
  return `<article class="source-panel"><div class="source-panel-head"><div class="source-icon">${icon('box')}</div><div><b>Services:</b> <span>${esc(srvRepo)}</span><br><b>Administration:</b> <span>${esc(admRepo)}</span><br><small style="color:var(--text-dim, #6e7681)">Click refresh to synchronize latest Git commits from main</small></div><button class="icon-button" data-refresh-github title="Sync latest commits from GitHub">${icon('refresh')}</button></div></article>`;
}
function releaseTable(rows){if(!rows.length)return empty('No releases have been created.',`<button class="text-action" data-open-release>Create release draft</button>`);return `<div class="table-wrap"><table><thead><tr><th>Component</th><th>Version</th><th>Commit</th><th>Validation</th><th>Status</th><th>Created</th><th></th></tr></thead><tbody>${rows.map(row=>`<tr><td><div class="component-cell">${icon('box')}<strong>${esc(row.component)}</strong></div></td><td><span class="version-chip">v${esc(row.version)}</span></td><td><span class="version-chip">${esc((row.git_commit_sha||'').slice(0,8))}</span></td><td>${badge(row.validation_status||'pending')}</td><td>${badge(row.status)}</td><td>${stamp(row.created_at)}</td><td><div class="row-actions">${row.validation_status!=='approved'||row.status==='draft'?`<button class="table-action primary-small" data-validate-release="${row.id}">Make Available</button>`:''}<button class="table-action" data-edit-notes="${row.id}">Notes</button>${row.status==='available'?`<button class="table-action primary-small" data-offer-release="${row.id}">Offer update</button><button class="table-action" data-supersede-release="${row.id}">Supersede</button>`:''}</div></td></tr>`).join('')}</tbody></table></div>`}

function renderClients(){return `${viewTitle('Client environments','Operational targets remain separate from their deployment history',`<button class="secondary view-action" data-open-client>${icon('plus')} Register client</button>`)}${panel('Environment registry','Installed versions, database providers, and connector health',clientTable(state.clients))}`}
function clientTable(rows){if(!rows.length)return empty('No client environments registered.',`<button class="text-action" data-open-deployment>Register one in a deployment</button>`);return `<div class="client-card-grid">${rows.map(row=>`<article class="client-card"><div class="client-card-top"><span class="client-logo large">${esc(row.name[0])}</span><div><h3>${esc(row.name)}</h3><p>${esc(row.code)}</p></div>${badge(row.environment)}</div><div class="client-health"><span class="health ${esc(row.last_health_status)}"><i></i>${esc(row.last_health_status)}</span><small>${esc(row.database_provider)}</small></div><dl><div><dt>Reporting Services</dt><dd>${esc(row.installed_services_version||'Not reported')}</dd></div><div><dt>Administration</dt><dd>${esc(row.installed_admin_version||'Not reported')}</dd></div></dl><button class="secondary configure-client" data-configure-client="${row.id}">${icon('settings')} Edit configuration</button></article>`).join('')}</div>`}

function renderTickets(){return `${viewTitle('Support ticket manager','Only issues approved by a client administrator are synchronized here')}${panel('Escalated tickets','Sanitized diagnostics and delivery lifecycle',ticketTable(state.tickets,false))}`}
function ticketTable(rows,compact){if(!rows.length)return empty('No client-approved tickets.');return `<div class="table-wrap"><table><thead><tr><th>Ticket</th><th>Client</th><th>Issue</th><th>Severity</th><th>Status</th>${compact?'':'<th>Approved by</th><th>Update</th>'}</tr></thead><tbody>${rows.map(row=>`<tr><td><strong>${esc(row.external_number)}</strong><small>${stamp(row.created_at)}</small></td><td>${esc(row.client_name)}</td><td><strong>${esc(row.title)}</strong><small>${esc(row.component)}</small></td><td>${badge(row.severity)}</td><td>${badge(row.status)}</td>${compact?'':`<td>${esc(row.approved_by)}</td><td><select class="ticket-status compact-select" data-ticket="${row.id}">${['escalated','triaged','in_progress','fix_available','deployed','closed'].map(value=>`<option ${row.status===value?'selected':''}>${value}</option>`).join('')}</select></td>`}</tr>`).join('')}</tbody></table></div>`}

function renderSettings(){
  const theme=document.documentElement.dataset.theme;const github=state.settings.github||{};const repositories=state.settings.repositories||{};const supabase=state.settings.storageBackend==='supabase-postgresql';
  return `${viewTitle('Manager settings','Configure this console without affecting client report environments')}<div class="settings-grid">
    <article class="settings-card"><div class="settings-icon">${icon('sun')}</div><div><h3>Appearance</h3><p>Choose the interface theme for this device.</p><div class="theme-options"><button data-theme-choice="light" class="${theme==='light'?'selected':''}">${icon('sun')} Light</button><button data-theme-choice="dark" class="${theme==='dark'?'selected':''}">${icon('moon')} Dark</button></div></div></article>
    <article class="settings-card source-setting"><div class="settings-icon purple">${icon('box')}</div><div><h3>GitHub release source</h3>${github.locked?`<p class="locked-source">${esc(github.repository)}</p><div class="locked-actions"><span class="protected">Locked</span><button type="button" class="action-btn-small" data-unlock-github>Edit source</button></div>`:`<p>Enter the code repository. It is automatically used for release discovery.</p><form id="githubSettingsForm"><input name="repository" type="url" required value="${esc(github.repository||'')}" placeholder="https://github.com/organisation/repository"><div class="form-actions"><button class="primary" type="submit">Save & lock</button>${github.repository?`<button class="secondary" type="button" data-cancel-github>Cancel</button>`:''}</div></form>`}</div></article>
    <article class="settings-card source-setting"><div class="settings-icon purple">${icon('grid')}</div><div><h3>Application repositories</h3>${repositories.locked?`<p class="locked-source"><b>Services</b><br>${esc(repositories.services_repository)}<br><br><b>Administration</b><br>${esc(repositories.admin_repository)}</p><div class="locked-actions"><span class="protected">Deployment source locked</span><button type="button" class="action-btn-small" data-unlock-repositories>Edit repositories</button></div>`:`<p>Set the two deployment repositories. The wizard will reuse these references.</p><form id="repositorySettingsForm" class="repository-form"><label class="form-label"><span>Reporting Services</span><input name="services_repository" type="url" required value="${esc(repositories.services_repository||'')}" placeholder="Reporting Services GitHub URL"></label><label class="form-label"><span>Administration</span><input name="admin_repository" type="url" required value="${esc(repositories.admin_repository||'')}" placeholder="Admin Console GitHub URL"></label><div class="form-actions"><button class="primary" type="submit">Save & lock</button>${repositories.services_repository?`<button class="secondary" type="button" data-cancel-repositories>Cancel</button>`:''}</div></form>`}</div></article>
    <article class="settings-card"><div class="settings-icon green">${icon('database')}</div><div><h3>Dedicated manager source</h3><p>${supabase?'Connected to the configured Supabase PostgreSQL source.':'Local SQLite is active. Add VTAB_MANAGER_DATABASE_URL to .env to use the dedicated Supabase source.'}</p><span class="${supabase?'protected':'planned'}">${supabase?'Supabase connected':'Setup required'}</span></div></article>
    <article class="settings-card"><div class="settings-icon pink">${icon('key')}</div><div><h3>Operator authentication</h3><p>A dedicated sign-in screen and identity provider integration are reserved for the next security phase.</p><span class="planned">Planned</span></div></article>
  </div>${panel('Audit trail','Release, deployment, ticket, and connector events',auditTable(state.audit),'audit-panel')}`;
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
  document.querySelectorAll('[data-supersede-release]').forEach(button=>button.onclick=()=>supersedeRelease(button.dataset.supersedeRelease));
  document.querySelectorAll('[data-sync-github]').forEach(button=>button.onclick=syncGitHubCandidates);
  document.querySelectorAll('[data-configure-client]').forEach(button=>button.onclick=()=>openConfiguration(button.dataset.configureClient));
  document.querySelectorAll('[data-approve]').forEach(button=>button.onclick=()=>approveDeployment(button.dataset.approve));
  document.querySelectorAll('[data-complete-deployment]').forEach(button=>button.onclick=()=>completeDeploymentAction(button.dataset.completeDeployment));
  document.querySelectorAll('[data-cancel-deployment]').forEach(button=>button.onclick=()=>cancelDeploymentAction(button.dataset.cancelDeployment));
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
  const githubForm=$('githubSettingsForm');if(githubForm)githubForm.onsubmit=saveGitHubSettings;
  const repositoryForm=$('repositorySettingsForm');if(repositoryForm)repositoryForm.onsubmit=saveRepositorySettings;
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
  const renderRow = $('renderServiceIdRow');
  if(renderRow){
    renderRow.style.display = isRender ? '' : 'none';
  }
  const srvId = item.provider_resource_id||item.config?.service_id||'';
  if(form.render_service_id){
    form.render_service_id.value = srvId;
  }
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
async function completeDeploymentAction(deploymentId){
  if(!confirm('Mark this deployment as Completed?'))return;
  try{
    await api(`/api/v1/operator/deployments/${deploymentId}/complete`,{method:'POST'});
    notify('Deployment marked completed.');
    await loadAll();
  }catch(error){
    notify(error.message,true);
  }
}
async function cancelDeploymentAction(deploymentId){
  if(!confirm('Are you sure you want to cancel this deployment offer and release any locks?'))return;
  try{
    await api(`/api/v1/operator/deployments/${deploymentId}/cancel`,{method:'POST'});
    notify('Deployment offer cancelled and lock released.');
    await loadAll();
  }catch(error){
    notify(error.message,true);
  }
}
async function saveConfiguration(event){event.preventDefault();const form=event.currentTarget;const button=event.submitter;setBusy(button,true,'Saving…');try{const provider_config=JSON.parse(form.provider_config_json.value||'{}');Object.assign(provider_config,{region:form.region.value,instance_size:form.instance_size.value,domain:form.domain.value});if(form.render_service_id&&form.render_service_id.value.trim()){provider_config.service_id=form.render_service_id.value.trim();}const environment_variables=JSON.parse(form.environment_variables_json.value||'{}');await api(`/api/v1/operator/clients/${form.client_id.value}/configuration`,{method:'PUT',body:JSON.stringify({infrastructure_id:form.infrastructure_id.value,provider_config,environment_variables,change_summary:form.change_summary.value,sync_provider:form.sync_provider.checked,provider_token:form.provider_token.value||environment_variables.RENDER_API_KEY||null})});form.provider_token.value='';form.closest('dialog').close();notify('Configuration saved as a new audited version.');await loadAll()}catch(error){notify(error.message,true)}finally{setBusy(button,false)}}
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
    notify(`${count} releases synchronized across Services & Administration.`);
    await loadAll();
  } catch(error) {
    notify(error.message, true);
  }
}
async function offerRelease(releaseId){
  if(!state.clients.length)return notify('Register a client before offering an update.',true);
  const choices=state.clients.map((item,index)=>`${index+1}. ${item.name}`).join('\n');
  const selected=Number(prompt(`Offer to which client?\n${choices}`,'1'))-1;
  const client=state.clients[selected];
  if(!client)return;
  const active=state.deployments.find(d=>d.client_id===client.id && !['succeeded','failed','cancelled','rolled_back'].includes(d.status));
  if(active){
    const activeVersion=active.target_version||'current';
    const statusLabel=active.status==='scheduled'?'Offered':active.status;
    const supersede=confirm(`Client "${client.name}" already has an active deployment (v${activeVersion}, ${statusLabel}).\n\nWould you like to cancel the existing offer and offer this new release instead?`);
    if(!supersede)return;
    try{
      await api(`/api/v1/operator/deployments/${active.id}/cancel`,{method:'POST'});
      notify('Previous offer cancelled. Offering new release…');
    }catch(err){
      return notify(`Failed to cancel existing deployment: ${err.message}`,true);
    }
  }
  try{
    await api('/api/v1/operator/deployments',{
      method:'POST',
      headers:{'Idempotency-Key':`offer:${client.id}:${releaseId}:${Date.now()}`},
      body:JSON.stringify({client_id:client.id,release_id:releaseId})
    });
    notify('Update offered. It will not run until the client administrator chooses Update Now.');
    await loadAll();
  }catch(error){
    notify(error.message,true);
  }
}
async function supersedeRelease(releaseId){const candidates=state.releases.filter(item=>item.id!==releaseId&&item.validation_status==='approved');if(!candidates.length)return notify('No approved replacement release is available.',true);const choices=candidates.map((item,index)=>`${index+1}. ${item.component} ${item.version}`).join('\n');const selected=Number(prompt(`Supersede with:\n${choices}`,'1'))-1;if(!candidates[selected])return;try{await api(`/api/v1/operator/releases/${releaseId}/supersede?superseded_by=${encodeURIComponent(candidates[selected].id)}`,{method:'POST'});notify('Release superseded. Existing running versions were not restarted.');await loadAll()}catch(error){notify(error.message,true)}}
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
  const environment_variables={};['SUPABASE_URL','VITE_SUPABASE_ANON_KEY','SUPABASE_SERVICE_ROLE_KEY','VTAB_WEB_URL','BREVO_API_KEY','ADMIN_EMAIL','VTAB_SMTP_FROM','ADMIN_OTP_ENABLED'].forEach(key=>{const value=data[`env_${key}`];if(value)environment_variables[key]=value});
  if(environment_variables.SUPABASE_URL)environment_variables.VITE_SUPABASE_URL=environment_variables.SUPABASE_URL;
  const clientId=state.wizardClientId||data.client_id;
  if(clientId)environment_variables.VTAB_CLIENT_ID=clientId;
  if(state.wizardBootstrapToken&&typeof state.wizardBootstrapToken==='string'){
    const m=state.wizardBootstrapToken.match(/vtab_tok_[a-f0-9]+/);
    if(m)environment_variables.VTAB_AGENT_TOKEN=m[0];
  }
  environment_variables.VTAB_SIMULATE_BUILD='0';
  const release_ids={};if(application!=='administration'&&data.services_release)release_ids.services=data.services_release;if(application!=='services'&&data.administration_release)release_ids.administration=data.administration_release;
  return {client_id:clientId,application,provider:data.provider||'render',release_ids,repositories:{services:repositories.services_repository||'',administration:repositories.admin_repository||''},provider_config:{service_name:data.service_name,region:data.region,instance_size:data.instance_size,owner_id:data.owner_id,service_id:data.service_id||'',branch:data.branch||'main',image:data.image||'ubuntu-24-04-x64',domain:data.domain||'',ssl:true},environment_variables};
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
function formData(form){return Object.fromEntries([...new FormData(form).entries()].filter(([,value])=>value!==''))}
function wireForm(id,handler){
  $(id).addEventListener('submit',async event=>{event.preventDefault();if(event.submitter?.value==='cancel'){event.currentTarget.closest('dialog').close();return}const button=event.submitter;setBusy(button,true);try{await handler(formData(event.currentTarget));event.currentTarget.reset();event.currentTarget.closest('dialog').close();await loadAll()}catch(error){notify(error.message,true)}finally{setBusy(button,false)}})
}
wireForm('clientForm',async data=>{const result=await api('/api/v1/operator/clients',{method:'POST',body:JSON.stringify(data)});$('bootstrapToken').textContent=`Connector token:\n${result.connectorBootstrapToken}\n\nClient administrator token:\n${result.clientAdminBootstrapToken}`;setTimeout(()=>$('tokenDialog').showModal(),100);notify('Client environment registered.')});
wireForm('releaseForm',async data=>{data.manifest={schema:'vtab-release-manifest',version:1};await api('/api/v1/operator/releases',{method:'POST',body:JSON.stringify(data)});notify('Release draft created.')});
$('configurationForm').addEventListener('submit',saveConfiguration);
$('deploymentForm').addEventListener('submit',async event=>{
  event.preventDefault();if(state.wizardStep!==6)return;const button=$('wizardDeploy');setBusy(button,true,'Starting…');
  try{
    const data=formData(event.currentTarget);if(!data.confirm_provisioning)throw new Error('Confirm that you authorize infrastructure provisioning.');
    const result=await api('/api/v1/operator/wizard/execute',{method:'POST',body:JSON.stringify({...wizardPlan(),provider_token:data.provider_token,confirm_provisioning:true})});
    ['provider_token','env_SUPABASE_SERVICE_ROLE_KEY','env_VITE_SUPABASE_ANON_KEY','env_BREVO_API_KEY'].forEach(name=>{const field=document.querySelector(`[name="${name}"]`);if(field)field.value=''});
    state.wizardJobs=result.jobs||[];$('deploymentJobs').innerHTML=state.wizardJobs.map(job=>`<article><span>${icon('rocket')}</span><div><b>${esc(job.component)}</b><small>${esc(job.deploymentId)}</small></div>${badge(job.status)}</article>`).join('');
    setWizardStep(7);await loadAll();notify('Deployment accepted and execution tracking started.');
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
