import { useEffect, useState } from "react";

type Update = { id:string; status:string; target_version:string; release_version:string; release_notes:string };

export function CloudWorkspaceUpdateBanner({ api, currentVersion, canUpdate }:{ api:string; currentVersion:string; canUpdate:boolean }) {
  const [update,setUpdate]=useState<Update|null>(null); const [busy,setBusy]=useState(false); const [hidden,setHidden]=useState(false);
  useEffect(()=>{ const poll=()=>fetch(`${api}/updates/available`).then(r=>r.ok?r.json():null).then(v=>setUpdate(v?.update||null)).catch(()=>{}); poll(); const timer=setInterval(poll,300000); return()=>clearInterval(timer)},[api]);
  if(!update||hidden)return null;
  async function apply(){if(!canUpdate||!confirm(`Install ${update!.release_version}?\n\n${update!.release_notes}`))return;setBusy(true);try{const response=await fetch(`${api}/updates/${update!.id}/accept`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({confirmed:true})});if(!response.ok)throw new Error(await response.text());}finally{setBusy(false)}}
  return <aside className="vtab-update-banner" role="status"><div><strong>Update available: {update.release_version}</strong><span>Installed: {currentVersion}</span><p>{update.release_notes}</p></div><button disabled={!canUpdate||busy} onClick={apply}>{busy?"Starting…":"Update Now"}</button><button onClick={()=>setHidden(true)}>Remind me later</button></aside>;
}

