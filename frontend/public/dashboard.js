(() => {
  const $ = id => document.getElementById(id)
  const config = new URLSearchParams(location.search)
  const api = config.get('api') || location.origin
  const finalContract = config.get('contract') === 'patterno'
  const labels = {unknown:'Unchecked',ok:'Reachable',error:'failed',integrating:'integrating',serving:'serving',healing:'healing',failed:'failed'}
  const checking = new Set()
  let attentionOnly = false
  let portalView = 'radar'
  let savedTenders = new Set()
  try { savedTenders = new Set(JSON.parse(localStorage.getItem('watchtower.savedTenders') || '[]')) } catch {}
  let swipe = null
  const needsAttention = portal => ['failed','error','healing'].includes(portal.state)
  const scopedPortals = () => attentionOnly ? portals.filter(needsAttention) : portals
  let portalSearch = ''
  const visiblePortals = () => scopedPortals().filter(portal=>
    [portal.name,portal.url].some(value=>value?.toLowerCase().includes(portalSearch))
  )
  function clearUnavailableSelection(){
    if(selected!==null&&!scopedPortals().some(portal=>String(portal.id)===selected))selected=null
  }
  let portals = [], selected = null, tab = 'tenders', detailRequest = 0, removal = null, loading = false, portalRevision = 0
  const escape = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c])
  const date = value => value ? new Date(value).toLocaleString() : '—'
  const badge = (state,label) => '<span class="pill '+escape(state)+'">'+escape(label)+'</span>'
  async function request(path,options){
    const response = await fetch(new URL(path,api),options)
    if(!response.ok){
      const body = await response.json().catch(()=>null)
      throw new Error(typeof body?.detail==='string' ? body.detail : 'API returned HTTP '+response.status)
    }
    if(response.status===204)return
    return response.json()
  }
  function normalize(portal){
    const state=portal.state ?? portal.status
    const reason=portal.reason ?? portal.detail ?? ''
    const statusCode=Number(portal.statusCode ?? portal.status_code)
    let accessFailure=''
    if(/\b(?:captcha|required human verification|verify (?:that )?you are human)\b/i.test(reason) && ['failed','error','healing'].includes(state)){
      accessFailure='The portal requires a CAPTCHA or human verification.'
    }else if(statusCode===401 || /\b(?:(?:log[ -]?in|sign[ -]?in|authentication) required|requires? (?:a )?(?:log[ -]?in|sign[ -]?in|authentication)|(?:log|sign) in to (?:continue|access))\b/i.test(reason)){
      accessFailure='The portal requires a login.'
    }else if(statusCode===403){
      accessFailure='The portal denied access (HTTP 403).'
    }
    return {id:portal.id,name:portal.name,location:portal.location,countryCode:portal.countryCode??portal.country_code,url:portal.url ?? portal.address,state:accessFailure?'failed':state,
      reason:accessFailure||reason,count:portal.tenderCount ?? portal.tender_count,
      last:portal.lastSuccessAt ?? portal.last_read_at,reachable:portal.reachable}
  }
  // Country label positions from Natural Earth, in longitude/latitude order.
  const countryPoints = {
    ae:[54.55,23.47], af:[66.5,34.16], al:[20.11,40.65], am:[44.8,40.46], ao:[17.98,-12.18], aq:[35.89,-79.84], ar:[-64.17,-33.5], at:[14.13,47.52],
    au:[134.05,-24.13], az:[47.21,40.4], ba:[18.07,44.09], bd:[89.68,24.21], be:[4.8,50.79], bf:[-1.36,12.67], bg:[25.16,42.51], bi:[29.92,-3.33],
    bj:[2.35,10.32], bn:[114.55,4.45], bo:[-64.59,-16.67], br:[-49.56,-12.1], bs:[-77.15,26.4], bt:[90.04,27.54], bw:[24.18,-22.1], by:[28.42,53.82],
    bz:[-88.71,17.2], ca:[-101.91,60.32], cd:[23.46,-1.86], cf:[20.91,6.99], cg:[15.9,0.14], ch:[7.46,46.72], ci:[-5.57,7.49], cl:[-72.32,-38.15],
    cm:[12.47,4.59], cn:[106.34,32.5], co:[-73.17,3.37], cr:[-84.08,10.07], cu:[-77.98,21.33], cy:[33.08,34.91], cz:[15.38,49.88], de:[9.68,50.96],
    dj:[42.5,11.98], dk:[9.02,55.97], do:[-70.65,19.1], dz:[2.81,27.4], ec:[-78.19,-1.26], ee:[25.87,58.72], eg:[29.45,26.19], eh:[-12.63,23.97],
    er:[38.29,15.79], es:[-3.46,40.09], et:[39.09,8.03], fi:[27.28,63.25], fj:[177.98,-17.83], fk:[-58.74,-51.61], fr:[2.55,46.7], ga:[11.84,-0.44],
    gb:[-2.12,54.4], ge:[43.74,41.87], gh:[-1.04,7.72], gl:[-39.34,74.32], gm:[-15.0,13.64], gn:[-10.02,10.62], gq:[8.99,2.33], gr:[21.73,39.49],
    gt:[-90.5,14.98], gw:[-14.52,12.16], gy:[-58.94,5.12], hn:[-86.89,14.79], hr:[16.37,45.81], ht:[-72.22,19.26], hu:[19.45,47.09], id:[101.89,-0.95],
    ie:[-7.8,53.08], il:[34.85,30.91], in:[79.36,22.69], iq:[43.26,33.09], ir:[54.93,32.17], is:[-18.67,64.78], it:[11.08,44.73], jm:[-77.32,18.14],
    jo:[36.38,30.81], jp:[138.44,36.14], ke:[37.91,0.55], kg:[74.53,41.67], kh:[104.5,12.65], kp:[126.44,39.89], kr:[128.13,36.38], kw:[47.31,29.41],
    kz:[68.69,49.05], la:[102.53,19.43], lb:[35.99,34.13], lk:[80.7,7.58], lr:[-9.46,6.45], ls:[28.25,-29.48], lt:[24.09,55.1], lu:[6.08,49.73],
    lv:[25.46,57.07], ly:[18.01,26.64], ma:[-7.19,31.65], md:[28.49,47.43], me:[19.14,42.8], mg:[46.7,-18.63], mk:[21.56,41.56], ml:[-2.04,18.69],
    mm:[95.8,21.57], mn:[104.15,46.0], mr:[-9.74,19.59], mw:[33.61,-13.39], mx:[-102.29,23.92], my:[113.84,2.53], mz:[37.84,-13.94], na:[17.11,-20.58],
    nc:[165.08,-21.06], ne:[9.5,17.45], ng:[7.5,9.44], ni:[-85.07,12.67], nl:[5.61,52.42], no:[9.68,61.36], np:[83.64,28.3], nz:[172.79,-39.76],
    om:[57.34,22.12], pa:[-80.35,8.72], pe:[-72.9,-12.98], pg:[143.91,-5.7], ph:[122.47,11.2], pk:[68.55,29.33], pl:[19.49,51.99], pr:[-66.48,18.23],
    ps:[35.29,32.05], pt:[-8.27,39.61], py:[-60.15,-21.67], qa:[51.14,25.24], ro:[24.97,45.73], rs:[20.79,44.19], ru:[44.69,58.25], rw:[30.1,-1.9],
    sa:[44.7,23.81], sb:[159.17,-8.03], sd:[29.26,16.33], se:[19.02,65.86], si:[14.92,46.06], sk:[19.05,48.73], sl:[-11.76,8.62], sn:[-14.78,15.14],
    so:[45.19,3.57], sr:[-55.91,4.14], ss:[30.39,7.23], sv:[-88.89,13.69], sy:[38.28,35.01], sz:[31.47,-26.53], td:[18.65,15.14], tf:[69.12,-49.3],
    tg:[1.06,8.81], th:[101.07,15.46], tj:[72.59,38.2], tl:[125.85,-8.8], tm:[58.68,39.86], tn:[9.01,33.69], tr:[34.51,39.35], tt:[-60.92,11.0],
    tw:[120.87,23.65], tz:[34.96,-6.05], ua:[32.14,49.72], ug:[32.95,1.97], us:[-97.48,39.54], uy:[-55.97,-32.96], uz:[64.01,41.69], ve:[-64.6,7.18],
    vn:[105.39,21.72], vu:[166.91,-15.37], xk:[20.86,42.59], ye:[45.87,15.33], za:[23.67,-29.71], zm:[26.4,-14.66], zw:[29.93,-18.91]
  }
  function portalPosition(portal){
    const {latitude,longitude}=portal.location??{}
    if(Number.isFinite(latitude)&&Math.abs(latitude)<=90&&Number.isFinite(longitude)&&Math.abs(longitude)<=180){
      return {latitude,longitude,approximate:false}
    }
    const suffix=new URL(portal.url).hostname.toLowerCase().replace(/\.$/,'').split('.').pop()
    const code=String(portal.countryCode??suffix).toLowerCase()
    if(!/^[a-z]{2}$/.test(code))return null
    const point=countryPoints[code==='uk'?'gb':code]
    return point?{longitude:point[0],latitude:point[1],approximate:true}:null
  }
  // Match the geographic extent of the Europe coastline asset.
  const europe = {west:-27,east:47,south:33,north:73}
  function onEuropeMap(position){
    return position&&position.longitude>=europe.west&&position.longitude<=europe.east
      &&position.latitude>=europe.south&&position.latitude<=europe.north
  }
  function radarDot(portal, position, offset=0){
    const state=portal.state
    const signal=({integrating:'integrating',serving:'serving',healing:'healing',failed:'failed',error:'failed'})[state]??'unknown'
    const pulse=state==='integrating'||(state==='serving'&&checking.has(String(portal.id)))?' is-checking':''
    const label=(portal.name||new URL(portal.url).hostname)+' · '+(labels[state]??state)
    const angle=offset*2.399963
    const radius=offset?Math.min(4,Math.sqrt(offset)*1.7):0
    const mapped=onEuropeMap(position)
    const mapX=mapped?(position.longitude-europe.west)*100/(europe.east-europe.west):0
    const mapY=mapped?(europe.north-position.latitude)*100/(europe.north-europe.south):0
    const x=Math.min(97,Math.max(3,mapX+Math.cos(angle)*radius))
    const y=Math.min(94,Math.max(6,mapY+Math.sin(angle)*radius*2))
    const coordinates=mapped?' style="--x:'+x+'%;--y:'+y+'%"':''
    const locationLabel=mapped?(position.approximate?' · Approximate location':''):position?' · Outside the map':' · Location unknown'
    return '<button type="button" class="radar-dot signal-'+signal+pulse+'"'+coordinates+' data-portal="'+escape(portal.id)+'" aria-pressed="'+(String(portal.id)===selected)+'" aria-label="'+escape(label+locationLabel)+'" title="'+escape(label+locationLabel)+'"></button>'
  }
  function updateMarkup(id, markup){
    const element=$(id)
    if(element.innerHTML!==markup)element.innerHTML=markup
  }
  function renderEuropeRadar(visible, empty){
    const mapDots=[]
    const unlocated=[]
    const occupied=new Map()
    const visibleIds=new Set(visible.map(portal=>portal.id))
    for(const portal of portals){
      const position=portalPosition(portal)
      if(!onEuropeMap(position)){
        if(visibleIds.has(portal.id))unlocated.push(radarDot(portal,position))
        continue
      }
      const key=position.longitude.toFixed(1)+','+position.latitude.toFixed(1)
      const offset=occupied.get(key)??0
      occupied.set(key,offset+1)
      if(visibleIds.has(portal.id))mapDots.push(radarDot(portal,position,offset))
    }
    updateMarkup('world-markers',mapDots.join(''))
    updateMarkup('unlocated-buttons',unlocated.join(''))
    $('unlocated-portals').hidden=!unlocated.length
    $('radar-empty').hidden=!!visible.length
    $('radar-empty').textContent=visible.length?'':empty
  }
  function renderList(){
    const totals = [['Monitored portals',portals.length],[labels.serving,portals.filter(p=>p.state==='serving').length],['Needs attention',portals.filter(needsAttention).length],['Tenders held',portals.every(p=>p.count!=null)?portals.reduce((sum,p)=>sum+p.count,0):'—']]
    const metrics=$('metrics')
    const focusedFilter=document.activeElement?.id==='attention-filter'
    const summary=totals.map(([name,value])=>{
      const content='<small>'+name+'</small><strong>'+value+'</strong>'
      return name==='Needs attention'
        ? '<button type="button" class="metric" id="attention-filter" aria-pressed="'+attentionOnly+'">'+content+'</button>'
        : '<div class="metric">'+content+'</div>'
    }).join('')
    if(metrics.innerHTML!==summary)metrics.innerHTML=summary
    if(focusedFilter)$('attention-filter').focus({preventScroll:true})
    const focus=document.activeElement?.dataset.portal
    const radar=portalView==='radar'
    $('view-radar').setAttribute('aria-pressed',radar)
    $('view-list').setAttribute('aria-pressed',!radar)
    $('radar-legend').hidden=!radar
    $('map-note').hidden=!radar
    $('portals').hidden=radar
    $('radar-view').hidden=!radar
    const visible=visiblePortals()
    const listRows=visible.map(p=>'<button data-portal="'+escape(p.id)+'" aria-pressed="'+(String(p.id)===selected)+'"><span class="row"><strong>'+escape(p.name||new URL(p.url).hostname)+'</strong>'+badge(labels[p.state]==='Reachable'?'reachable':p.state,labels[p.state]??p.state)+'</span><small>'+escape(p.url)+'</small>'+(needsAttention(p)&&p.reason?'<small class="portal-summary">'+escape(p.reason)+'</small>':'')+'<small class="portal-summary">'+escape(p.count??'—')+' tenders · Last successful check '+escape(date(p.last))+'</small></button>').join('')
    const empty=portalSearch?'No portals match your search.':attentionOnly?'No portals need attention.':'No portals yet. Add one to get started.'
    if(radar)renderEuropeRadar(visible,empty)
    else updateMarkup('portals',listRows||'<p>'+empty+'</p>')
    if(focus)Array.from($('portal-picker').querySelectorAll('button')).find(button=>button.dataset.portal===focus&&!button.closest('[hidden]'))?.focus({preventScroll:true})
  }
  function runLabel(status){
    return ({ok:'Succeeded',error:'Failed',failed:'Failed'})[status]??status??'In progress'
  }
  function runTime(run){
    return run.finishedAt ?? run.at ?? run.startedAt
  }
  function timelineEvent(title, timestamp, detail, duration){
    return '<li class="entry"><h3>'+escape(title)+'</h3>'
      +(timestamp?'<small>'+escape(date(timestamp))+(duration!=null?' · '+escape(duration):'')+'</small>':'')
      +(detail?'<p>'+escape(detail)+'</p>':'')+'</li>'
  }
  function renderTimeline(runs, portal){
    const events=[...runs].reverse().map(run=>{
      const status=runLabel(run.result??run.status).toLowerCase()
      const kind=run.kind==='repair'?'Repair':'Read'
      const elapsed=run.durationMs??run.duration_ms??(run.startedAt&&run.finishedAt?Date.parse(run.finishedAt)-Date.parse(run.startedAt):null)
      const duration=Number.isFinite(elapsed)&&elapsed>=0?elapsed+' ms':null
      const started=run.kind==='repair'&&run.startedAt&&status!=='in progress'
        ? timelineEvent('Repair started',run.startedAt):''
      return started+timelineEvent(status==='in progress'?kind+' started':kind+' '+status,runTime(run),run.detail,duration)
    })
    if(portal.state==='healing'&&runs[0]?.kind!=='repair'){
      events.push(timelineEvent('Repair pending',null,portal.reason))
    }
    return events.length?'<ol class="timeline">'+events.join('')+'</ol>':'<p>No runs recorded yet.</p>'
  }
  function filterTenders(tenders, cutoff, status){
    return tenders.filter(tender=>(!status||tender.status===status)&&(!cutoff||finalContract||Date.parse(tender.lastChangedAt)>Date.parse(cutoff)))
  }
  function documentLink(doc){
    try{
      const source=doc.path ?? doc.url ?? doc.downloadPath
      if(!source)return ''
      const url=new URL(source,api)
      if(!['http:','https:'].includes(url.protocol))return ''
      return '<div><a href="'+escape(url.href)+'" download target="_blank" rel="noopener">Download '+escape(doc.fileName??'document')+'</a></div>'
    }catch{return ''}
  }
  function renderTender(tender, portal){
    const identifier=(finalContract?tender.url??tender.reference:tender.reference??tender.url)??tender.id??tender.title
    const key=JSON.stringify([portal.url,identifier])
    const saved=savedTenders.has(key)
    const address=tender.address??tender.url
    let addressMarkup='—'
    if(address){
      addressMarkup=escape(address)
      try{
        const url=new URL(address,api)
        if(['http:','https:'].includes(url.protocol))addressMarkup='<a href="'+escape(url.href)+'" target="_blank" rel="noopener">'+escape(address)+'</a>'
      }catch{}
    }
    const fields=[
      ['Identifier',escape(tender.reference??tender.id??'—')],
      ['Address',addressMarkup],
      ['Buyer',escape(tender.buyerName??'—')],
      ['Status',escape(tender.status??'—')],
      ['Submissions open',escape(date(tender.submissionOpensAt))],
      ['Submission deadline',escape(date(tender.submissionDeadline))],
      ['Last changed',escape(date(tender.lastChangedAt))]
    ]
    const documents=(tender.documents??[]).map(documentLink).join('')
    return '<article class="entry tender-card" data-tender="'+escape(key)+'"><div class="row"><h3>'+escape(tender.title??'Untitled tender')+'</h3>'
      +'<button type="button" data-save aria-pressed="'+saved+'">'+(saved?'Saved':'Save')+'</button></div>'
      +'<dl class="tender-fields">'+fields.map(([label,value])=>'<div><dt>'+label+'</dt><dd>'+value+'</dd></div>').join('')+'</dl>'
      +'<h4>Documents</h4>'+(documents||'<p>No documents listed.</p>')+'</article>'
  }
  function setTenderSaved(card, saved){
    const key=card.dataset.tender
    if(saved)savedTenders.add(key)
    else savedTenders.delete(key)
    try { localStorage.setItem('watchtower.savedTenders',JSON.stringify([...savedTenders])) } catch {}
    const button=card.querySelector('[data-save]')
    button.setAttribute('aria-pressed',saved)
    button.textContent=saved?'Saved':'Save'
  }
  async function renderDetail(){
    const token=++detailRequest
    const p=portals.find(item=>String(item.id)===selected)
    $('controls').hidden=!p
    $('portal-views').hidden=!p
    $('result-tools').hidden=!p||tab!=='tenders'
    $('tender-filters').hidden=!p||tab!=='tenders'
    if(!p){$('details').innerHTML='<p>Select a portal to see its details.</p>';$('entries').innerHTML='';return}
    const reachability=p.reachable==null?'':`<p>Website ${p.reachable?'reachable':'unreachable'}</p>`
    $('details').innerHTML='<h2>'+escape(p.name||new URL(p.url).hostname)+'</h2><small id="portal-url">'+escape(p.url)+'</small>'+reachability+'<p>'+escape(p.reason||labels[p.state]||p.state)+'</p><small id="latest-check" hidden></small><small style="display:block">Last successful read · '+date(p.last)+'</small>'+(p.last&&p.state==='healing'?'<p class="repair-banner">Showing tenders from the last successful check · '+escape(date(p.last))+'</p>':p.last&&p.state==='failed'?'<p>Showing tenders from the last verified read.</p>':'')
    $('tenders').setAttribute('aria-pressed',tab==='tenders')
    $('runs').setAttribute('aria-pressed',tab==='runs')
    $('check').disabled=checking.has(selected)||p.state==='integrating'||p.state==='healing'
    $('check').textContent=checking.has(selected)?'Checking…':'Check now'
    const path='/portals/'+encodeURIComponent(p.id)
    const history=request(path+'/runs').then(runs=>[...runs].sort((a,b)=>new Date(runTime(b))-new Date(runTime(a))))
    const latest=history.then(([run])=>{
      if(token!==detailRequest||!run||!runTime(run))return
      const status=run.result??run.status
      const kind=run.kind==='repair'?'repair':'check'
      $('latest-check').textContent=(['error','failed'].includes(status)?`Last ${kind} failed`:`Last ${kind}`)+' · '+date(runTime(run))
      $('latest-check').hidden=false
    }).catch(()=>{})
    try{
      const recent=$('tender-since').value==='24h'
      const cutoff=recent?new Date(Date.now()-86_400_000).toISOString():null
      const query=finalContract&&cutoff?'?'+new URLSearchParams({changedSince:cutoff}):''
      const entries=await (tab==='runs'?history:request(path+'/tenders'+query))
      if(token!==detailRequest)return
      let tenders=entries
      if(tab==='tenders'){
        const hasDates=finalContract||entries.some(tender=>tender.lastChangedAt)
        const hasStatuses=finalContract||entries.some(tender=>tender.status)
        $('tender-since').disabled=!hasDates
        $('tender-status').disabled=!hasStatuses
        $('filter-note').textContent=!hasDates&&!hasStatuses?'This API does not provide tender change dates or statuses.':!hasDates?'This API does not provide tender change dates.':!hasStatuses?'This API does not provide tender statuses.':''
        tenders=filterTenders(entries,hasDates?cutoff:null,hasStatuses?$('tender-status').value:null)
      }
      const markup=tab==='runs'
        ? renderTimeline(entries,p)
        : tenders.map(t=>renderTender(t,p)).join('') || (entries.length||query?'<p>No tenders match these filters.</p>':'<p>No tenders available yet.</p>')
      if($('entries').innerHTML!==markup)$('entries').innerHTML=markup
    }catch(error){if(token===detailRequest)$('entries').textContent='Could not load '+tab+'. '+error.message}
    await latest
  }
  async function load(){
    if(loading)return
    loading=true
    const revision=portalRevision
    try{
      const results=await request('/portals')
      if(revision!==portalRevision)return
      portals=results.map(normalize)
      clearUnavailableSelection()
      $('message').textContent=''
      renderList()
      await renderDetail()
    }catch(error){$('message').textContent='Could not reach the portal API. '+error.message}
    finally{loading=false}
  }
  $('metrics').onclick=event=>{
    if(!event.target.closest('#attention-filter'))return
    attentionOnly=!attentionOnly
    clearUnavailableSelection()
    $('action-message').textContent=''
    $('entries').textContent=''
    renderList()
    renderDetail()
  }
  for(const view of ['radar','list'])$('view-'+view).onclick=()=>{
    portalView=view
    renderList()
  }
  $('entries').onclick=event=>{
    const button=event.target.closest('[data-save]')
    if(button)setTenderSaved(button.closest('[data-tender]'),button.getAttribute('aria-pressed')!=='true')
  }
  $('entries').onpointerdown=event=>{
    if(!event.isPrimary||event.button!==0||event.target.closest('a,button'))return
    const card=event.target.closest('[data-tender]')
    if(!card)return
    swipe={id:event.pointerId,x:event.clientX,y:event.clientY,card}
    $('entries').setPointerCapture(event.pointerId)
  }
  $('entries').onpointerup=event=>{
    if(swipe?.id!==event.pointerId)return
    const {x,y,card}=swipe
    swipe=null
    if(card.isConnected&&event.clientX-x>65&&event.clientX-x>Math.abs(event.clientY-y)*1.5)setTenderSaved(card,true)
  }
  $('entries').onpointercancel=event=>{if(swipe?.id===event.pointerId)swipe=null}
  $('portal-search').oninput=event=>{
    portalSearch=event.target.value.trim().toLowerCase()
    renderList()
  }
  $('portal-picker').onclick=event=>{
    const button=event.target.closest('[data-portal]')
    if(!button)return
    selected=selected===button.dataset.portal?null:button.dataset.portal
    $('action-message').textContent=''
    $('entries').textContent='Loading…'
    renderList()
    renderDetail()
  }
  for(const name of ['tenders','runs'])$(name).onclick=()=>{tab=name;$('entries').textContent='Loading…';renderDetail()}
  for(const id of ['tender-since','tender-status'])$(id).onchange=renderDetail
  $('add').onclick=()=>{$('form').reset();$('form-error').textContent='';$('dialog').showModal()}
  $('cancel').onclick=()=>$('dialog').close()
  $('form').onsubmit=async event=>{
    event.preventDefault()
    $('form-error').textContent=''
    $('submit').disabled=true
    try{
      const address=$('url').value.trim()
      const url=new URL(/^[a-z][a-z\d+.-]*:\/\//i.test(address) ? address : 'https://'+address)
      if(!['https:','http:'].includes(url.protocol)||url.username||url.password)throw new Error('Enter a public HTTP or HTTPS URL.')
      const created=await request('/portals',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({[finalContract?'url':'address']:url.href})})
      const portal=created.address||created.url?created:await request('/portals/'+encodeURIComponent(created.id))
      portalRevision++
      portals=portals.filter(item=>String(item.id)!==String(portal.id))
      portals.push(normalize(portal))
      attentionOnly=false
      portalSearch=''
      $('portal-search').value=''
      selected=String(portal.id)
      tab='tenders'
      $('dialog').close()
      $('entries').textContent='Loading…'
      renderList()
      await renderDetail()
    }catch(error){$('form-error').textContent=error.message}
    finally{$('submit').disabled=false}
  }
  $('check').onclick=async()=>{
    if(!selected||$('check').disabled)return
    const id=selected
    checking.add(id)
    renderList()
    $('action-message').textContent=''
    $('check').disabled=true
    $('check').textContent='Checking…'
    try{
      await request('/portals/'+encodeURIComponent(id)+'/refresh',{method:'POST'})
      await load()
    }catch(error){
      if(selected===id)$('action-message').textContent='Check failed. '+error.message
    }finally{
      checking.delete(id)
      renderList()
      if(selected===id){
        const portal=portals.find(item=>String(item.id)===id)
        $('check').disabled=['integrating','healing'].includes(portal?.state)
        $('check').textContent='Check now'
      }
    }
  }
  $('remove').onclick=()=>{
    const portal=portals.find(item=>String(item.id)===selected)
    if(!portal)return
    removal=selected
    $('remove-description').textContent='Stop monitoring '+portal.url+' and remove its tender and run history?'
    $('remove-error').textContent=''
    $('remove-dialog').showModal()
  }
  $('remove-cancel').onclick=()=>$('remove-dialog').close()
  $('remove-dialog').oncancel=event=>{
    if($('remove-confirm').disabled)event.preventDefault()
  }
  $('remove-form').onsubmit=async event=>{
    event.preventDefault()
    if(removal===null||$('remove-confirm').disabled)return
    const id=removal
    $('remove-confirm').disabled=true
    $('remove-cancel').disabled=true
    $('remove-error').textContent=''
    try{
      await request('/portals/'+encodeURIComponent(id),{method:'DELETE'})
      portalRevision++
      portals=portals.filter(portal=>String(portal.id)!==id)
      if(selected===id){
        selected=null
        tab='tenders'
        $('entries').textContent=''
        $('action-message').textContent=''
      }
      $('remove-dialog').close()
      renderList()
      await renderDetail()
      if(selected===null)$('add').focus()
      else $(portalView==='radar'?'radar-view':'portals').querySelector('[aria-pressed="true"]')?.focus()
    }catch(error){
      $('remove-error').textContent=error.message
    }finally{
      $('remove-confirm').disabled=false
      $('remove-cancel').disabled=false
    }
  }
  setInterval(load,10_000)
  load()
})();
