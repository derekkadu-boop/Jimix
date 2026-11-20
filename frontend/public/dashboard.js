(() => {
  const $ = id => document.getElementById(id)
  const day = 86_400_000
  const text = (id, value) => { if ($(id).textContent !== String(value)) $(id).textContent = value }
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[c])
  const utcFormat = new Intl.DateTimeFormat('en-GB', { timeZone:'UTC', day:'numeric', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit' })
  const shortFormat = new Intl.DateTimeFormat('en-GB', { timeZone:'UTC', day:'numeric', month:'short' })
  const clockFormat = new Intl.DateTimeFormat('en-GB', { timeZone:'UTC', hour:'2-digit', minute:'2-digit' })
  const time = value => typeof value === 'number' ? value : Date.parse(value)
  const date = value => Number.isFinite(time(value)) ? `${utcFormat.format(time(value))} UTC` : '—'
  const dateOnly = value => /^\d{4}-\d{2}-\d{2}$/.test(value ?? '')
  const zoned = value => /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value ?? '')
  const deadlineDate = value => {
    if (!Number.isFinite(time(value))) return '—'
    if (dateOnly(value)) return `${shortFormat.format(time(value))} ${value.slice(0,4)} · date only`
    return zoned(value) ? date(value) : `${value.replace('T',' ')} · source timezone unspecified`
  }
  const deadlineTime = value => dateOnly(value) ? time(value) + day - 1 : zoned(value) ? time(value) : NaN
  const count = value => value.toLocaleString('en-GB')
  const labels = { serving:'Serving', integrating:'Integrating', healing:'Healing', failed:'Failed', unknown:'Unchecked' }
  const stateOf = p => p.state === 'error' ? 'failed' : p.state === 'ok' ? 'serving' : Object.hasOwn(labels,p.state) ? p.state : 'unknown'
  const badge = (state, label) => `<span class="pill ${escape(state)}">${escape(label)}</span>`
  const portalBadge = p => badge(stateOf(p), labels[stateOf(p)])
  const needsAttention = p => ['failed','healing'].includes(stateOf(p))
  const safeUrl = (address, base = location.origin) => {
    if (!address) return null
    try { const url = new URL(address, base); return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url : null } catch { return null }
  }
  const nameOf = p => p.name || safeUrl(p.url)?.hostname || p.url || 'Unnamed portal'
  const link = (address, title, base) => {
    const url = safeUrl(address, base)
    return url ? `<a href="${escape(url.href)}" target="_blank" rel="noopener noreferrer">${escape(title || address)}</a>` : escape(title || address || '—')
  }
  const path = portal => `/portals/${encodeURIComponent(portal.id)}`
  const runKind = run => run.kind === 'repair' ? 'Repair' : 'Read'
  const outcome = run => ({ ok:'Succeeded', failed:'Failed', error:'Failed' })[run.result] ?? (run.finishedAt ? 'Outcome unknown' : 'In progress')
  const runStamp = run => time(run.finishedAt ?? run.startedAt)
  const runBadge = run => badge(({ Succeeded:'ok', Failed:'failed', 'In progress':'active' })[outcome(run)] ?? '', `${runKind(run)} · ${outcome(run)}`)
  const startOfDay = value => Math.floor(value / day) * day
  const key = (...parts) => escape(JSON.stringify(parts))

  let portals = [], selected = null, tab = 'tenders', scope = '', listAt = null, listError = false, loading = false, mutation = 0
  let capabilities = {}, capabilitiesAt = 0, removal = null
  const checking = new Set()
  const data = { runs:new Map(), tenders:new Map() }
  const intervals = { runs:10_000, tenders:30_000 }
  const chartRecords = new Map()
  let activeRecords = [], deadlineRecords = {}, drilldown = null
  const emptyDetail = $('details').innerHTML

  async function request(url, options) {
    const response = await fetch(url, { cache:'no-store', ...options, signal:AbortSignal.timeout(8_000) })
    if (!response.ok) {
      const body = await response.json().catch(() => null)
      throw new Error(typeof body?.detail === 'string' ? body.detail : `HTTP ${response.status}`)
    }
    return response.status === 204 ? null : response.json()
  }
  async function list(url) {
    const records = await request(url)
    if (!Array.isArray(records)) throw new Error('Unexpected API response')
    return records
  }

  function patchNode(element, next) {
    if (!element || element.nodeName !== next.nodeName) {
      if (element) element.replaceWith(next)
      return next
    }
    if (element.nodeType === Node.TEXT_NODE) {
      if (element.data !== next.data) element.data = next.data
      return element
    }
    for (const attr of element.getAttributeNames()) {
      if (attr !== 'open' && !next.hasAttribute(attr)) element.removeAttribute(attr)
    }
    for (const attr of next.getAttributeNames()) {
      if (element.getAttribute(attr) !== next.getAttribute(attr)) element.setAttribute(attr, next.getAttribute(attr))
    }
    patchChildren(element, next)
    return element
  }
  function patchChildren(element, source) {
    const children = [...source.childNodes]
    const old = [...element.childNodes]
    const keyed = new Map(old.filter(node => node.dataset?.key).map(node => [node.dataset.key, node]))
    const kept = new Set(children.map(node => node.dataset?.key).filter(Boolean))
    for (const node of old) if (node.dataset?.key && !kept.has(node.dataset.key)) node.remove()
    let cursor = element.firstChild
    for (const next of children) {
      const id = next.dataset?.key
      const previous = id ? keyed.get(id) : cursor?.dataset?.key ? null : cursor
      if (!previous) { element.insertBefore(next, cursor); continue }
      if (previous !== cursor) {
        const focused = previous.contains(document.activeElement) ? document.activeElement : null
        element.insertBefore(previous, cursor)
        focused?.focus({ preventScroll:true })
      }
      cursor = patchNode(previous, next).nextSibling
    }
    while (cursor) { const next = cursor.nextSibling; cursor.remove(); cursor = next }
  }
  function markup(id, html) {
    const element = $(id)
    if (element.innerHTML === html) return
    const template = document.createElement('template')
    template.innerHTML = html
    patchChildren(element, template.content)
  }
  function animate(id) {
    const element = $(id)
    element.classList.remove('view-enter')
    void element.offsetWidth
    element.classList.add('view-enter')
  }

  // Reuse the Europe coastline asset and Natural Earth country label positions.
  const extent = { west:-27, east:47, south:33, north:73 }
  const countryPoints = {
    al:[20.11,40.65], at:[14.13,47.52], ba:[18.07,44.09], be:[4.8,50.79], bg:[25.16,42.51], by:[28.42,53.82], ch:[7.46,46.72], cy:[33.08,34.91], cz:[15.38,49.88], de:[9.68,50.96], dk:[9.02,55.97], dz:[2.81,27.4], ee:[25.87,58.72], es:[-3.46,40.09], fi:[27.28,63.25], fr:[2.55,46.7], gb:[-2.12,54.4], ge:[43.74,41.87], gr:[21.73,39.49], hr:[16.37,45.81], hu:[19.45,47.09], ie:[-7.8,53.08], is:[-18.67,64.78], it:[11.08,44.73], lt:[24.09,55.1], lu:[6.08,49.73], lv:[25.46,57.07], md:[28.49,47.43], me:[19.14,42.8], mk:[21.56,41.59], nl:[5.61,52.42], no:[9.68,61.36], pl:[19.49,51.99], pt:[-8.27,39.61], ro:[24.97,45.73], rs:[20.79,44.19], ru:[44.69,58.25], se:[19.02,65.86], si:[14.92,46.06], sk:[19.05,48.73], tn:[9.01,33.69], tr:[34.51,39.35], ua:[32.14,49.72], xk:[20.86,42.59], mt:[14.4,35.9], ad:[1.6,42.5], mc:[7.4,43.7], li:[9.55,47.15], sm:[12.45,43.95], va:[12.45,41.9], fo:[-6.9,62.1]
  }
  function position(p) {
    const { latitude, longitude, countryCode } = p.location ?? {}
    if (Number.isFinite(latitude) && Math.abs(latitude) <= 90 && Number.isFinite(longitude) && Math.abs(longitude) <= 180) return [longitude,latitude]
    const suffix = (countryCode || safeUrl(p.url)?.hostname.split('.').filter(Boolean).at(-1) || '').toLowerCase()
    return countryPoints[suffix === 'uk' ? 'gb' : suffix]
  }
  function registration(p) { return [p.location?.city, p.location?.country].filter(Boolean).join(', ') }
  function portalButton(p, content, extra = '') {
    return `<button type="button" data-key="${key(p.id)}" data-portal="${escape(p.id)}" aria-pressed="${selected === String(p.id)}" ${extra}>${content}</button>`
  }
  function renderRadar() {
    const markers = [], unlocated = [], locations = new Map()
    for (const p of portals) {
      const [lon,lat] = position(p) ?? []
      if (lon >= extent.west && lon <= extent.east && lat >= extent.south && lat <= extent.north) {
        const point = `${lon.toFixed(1)},${lat.toFixed(1)}`
        const n = locations.get(point) ?? 0
        locations.set(point,n+1)
        const r = n ? Math.min(4,Math.sqrt(n)*1.7) : 0
        const x = Math.min(97,Math.max(3,(lon-extent.west)*100/(extent.east-extent.west)+Math.cos(n*2.4)*r))
        const y = Math.min(95,Math.max(5,(extent.north-lat)*100/(extent.north-extent.south)+Math.sin(n*2.4)*r*2))
        const label = [nameOf(p),labels[stateOf(p)],registration(p),'Approximate registration location'].filter(Boolean).join(' · ')
        markers.push(portalButton(p,'',`class="radar-dot signal-${stateOf(p)}" style="--x:${x}%;--y:${y}%" aria-label="${escape(label)}" title="${escape(label)}"`))
      } else {
        unlocated.push(`<li data-key="${key(p.id)}">${portalButton(p,`${escape(nameOf(p))} ${portalBadge(p)}`)}</li>`)
      }
    }
    markup('world-markers',markers.join(''))
    markup('unlocated-buttons',unlocated.join(''))
    $('unlocated-portals').hidden = !unlocated.length
    text('unlocated-count',unlocated.length)
    $('radar-empty').hidden = !!markers.length
    text('radar-empty',listAt ? 'No mapped portals yet.' : listError ? 'Portal locations unavailable.' : 'Loading portal locations…')
  }

  function renderPortals() {
    $('sync-status').dataset.state = listError ? 'offline' : listAt ? 'live' : 'connecting'
    text('sync-label',listError ? listAt ? 'Showing cached portal health' : 'API unavailable' : listAt ? 'API connected' : 'Connecting to API')
    text('message',listError ? listAt ? `Last synced ${date(listAt)}. Retrying automatically.` : 'Portal data will refresh when the connection returns.' : '')
    const attention = portals.filter(needsAttention).sort((a,b) => Number(stateOf(b) === 'failed') - Number(stateOf(a) === 'failed'))
    for (const button of $('metrics').querySelectorAll('[data-status]')) {
      const status = button.dataset.status
      const value = listAt ? count(status ? portals.filter(p => stateOf(p) === status).length : portals.length) : '—'
      if (button.querySelector('strong').textContent !== value) button.querySelector('strong').textContent = value
      button.setAttribute('aria-pressed',String(scope === status))
    }
    text('attention-count',listAt ? count(attention.length) : '—')
    text('attention-summary',listAt ? attention.length ? `${attention.filter(p => stateOf(p) === 'failed').length} failed · ${attention.filter(p => stateOf(p) === 'healing').length} healing` : 'No portals are failing or healing.' : listError ? 'Portal health is unavailable.' : 'Checking portal health…')
    $('attention-filter').setAttribute('aria-pressed',String(scope === 'attention'))
    markup('attention-portals',attention.slice(0,3).map(p => `<li data-key="${key(p.id)}">${portalButton(p,`${escape(nameOf(p))} ${portalBadge(p)}`)}</li>`).join(''))
    const search = $('portal-search').value.trim().toLowerCase()
    const visible = portals.filter(p => (!scope || (scope === 'attention' ? needsAttention(p) : stateOf(p) === scope)) && [nameOf(p),p.url].some(v => v?.toLowerCase().includes(search)))
    markup('portals',visible.map(p => portalButton(p,`<span class="row"><strong>${escape(nameOf(p))}</strong>${portalBadge(p)}</span><span class="portal-row-meta"><small>${escape(p.url)}</small><small>${escape(p.tenderCount ?? '—')} tenders</small></span>`)).join('') || `<p>${!listAt ? listError ? 'Portal list unavailable.' : 'Loading portals…' : search || scope ? 'No portals match these filters.' : 'No portals yet. Add one to start monitoring.'}</p>`)
    renderRadar()
  }

  function coverage(kind) {
    if (!listAt) return listError ? 'Portal API unavailable.' : 'Loading portal data…'
    const records = portals.map(p => data[kind].get(String(p.id)))
    const missing = records.filter(r => !r?.items).length
    const pending = records.filter(r => !r || (!r.items && !r.error)).length
    const stale = records.filter(r => r?.items && (r.error || listError))
    if (records.length && missing === records.length && !pending) return 'Data unavailable for all monitored portals.'
    return [pending ? `Loading ${pending} of ${records.length} portals.` : '', missing > pending ? `Partial coverage: ${missing - pending} of ${records.length} portals unavailable.` : '', stale.length ? `Cached data for ${stale.length} of ${records.length} portals · oldest update ${date(Math.min(...stale.map(r => r.at)))}.` : '', listError && !stale.length ? `Portal inventory last synced ${date(listAt)}.` : ''].filter(Boolean).join(' ')
  }
  function hasCoverage(kind) { return !!listAt && (!portals.length || portals.some(p => data[kind].get(String(p.id))?.items)) }
  function allRecords(kind) {
    return portals.flatMap(portal => (data[kind].get(String(portal.id))?.items ?? []).map(item => ({ portal,item })))
  }

  const outcomes = ['Succeeded','Failed','Outcome unknown']
  const kinds = ['Read','Repair']
  function initChart() {
    const bars = kinds.map((kind,k) => `<div class="chart-track" aria-label="${kind}">${outcomes.map((result,o) => `<button type="button" class="chart-segment signal-${['serving','failed','unknown'][o]}" data-slot="${k}-${o}" hidden></button>`).join('')}</div>`).join('')
    markup('run-chart','<div class="chart-axis" aria-hidden="true"><span id="chart-high">—</span><span>0</span></div>' + Array.from({length:7},(_,index) => `<div class="chart-day" data-index="${index}"><div class="chart-bars">${bars}</div><small aria-hidden="true"></small></div>`).join(''))
  }
  function renderRuns(now) {
    const end = startOfDay(now)
    const start = end - 6*day
    const records = allRecords('runs').filter(({ item }) => ['read','repair'].includes(item.kind))
    const completed = records.filter(({ item }) => Number.isFinite(time(item.finishedAt)) && time(item.finishedAt) >= start && time(item.finishedAt) <= now)
    const successful = completed.filter(({item}) => outcome(item) === 'Succeeded').length
    activeRecords = records.filter(({item}) => !item.finishedAt && !item.result)
    const covered = hasCoverage('runs')
    text('active-runs',`${covered ? count(activeRecords.length) : '—'} active`)
    $('active-runs').disabled = !activeRecords.length
    text('success-rate',completed.length ? `${Math.round(100*successful/completed.length)}%` : '—')
    const runCoverage = coverage('runs')
    const unavailable = listError || runCoverage.startsWith('Data unavailable')
    text('run-denominator',completed.length ? `${count(successful)} of ${count(completed.length)} completed` : covered ? 'No completed runs · 0 total' : unavailable ? 'Completed runs unavailable' : 'Awaiting completed run data')
    text('chart-coverage',runCoverage)
    chartRecords.clear()
    for (const record of completed) {
      const stamp = startOfDay(time(record.item.finishedAt))
      const id = `${stamp}:${kinds.indexOf(runKind(record.item))}-${outcomes.indexOf(outcome(record.item))}`
      if (!chartRecords.has(id)) chartRecords.set(id,[])
      chartRecords.get(id).push(record)
    }
    const columns = [...$('run-chart').querySelectorAll('.chart-day')]
    const maximum = Math.max(1,...columns.flatMap((_,i) => kinds.map((_,k) => outcomes.reduce((sum,_,o) => sum+(chartRecords.get(`${start+i*day}:${k}-${o}`)?.length ?? 0),0))))
    text('chart-high',covered ? maximum : '—')
    columns.forEach((column,i) => {
      const stamp = start+i*day
      column.dataset.stamp = stamp
      const label = shortFormat.format(stamp)
      const tick = column.querySelector('small')
      if (tick.textContent !== label) tick.textContent = label
      for (const element of column.querySelectorAll('[data-slot]')) {
        const id = `${stamp}:${element.dataset.slot}`
        const [k,o] = element.dataset.slot.split('-').map(Number)
        const total = chartRecords.get(id)?.length ?? 0
        const caption = `${label} UTC · ${kinds[k]} · ${outcomes[o]}: ${count(total)}`
        element.hidden = !total
        element.style.height = `${total / maximum * 100}%`
        element.title = caption
        element.setAttribute('aria-label',caption)
      }
    })
    const events = records.flatMap(({portal,item}) => {
      const at = time(item.startedAt), end = time(item.finishedAt)
      const recent = stamp => stamp >= now-day && stamp <= now
      return [recent(at) && (!item.finishedAt || item.kind === 'repair') && { portal,item,stamp:at,start:true }, recent(end) && { portal,item,stamp:end,start:false }].filter(Boolean)
    }).sort((a,b) => b.stamp-a.stamp).slice(0,5)
    markup('recap-events',events.map(({portal,item,stamp,start}) => `<li data-key="${key(portal.id,item.id,start)}">${portalButton(portal,`<span>${escape(nameOf(portal))}</span><small>${clockFormat.format(stamp)}</small>${start ? badge('active',`${runKind(item)} started`) : runBadge(item)}`)}</li>`).join(''))
    text('recap-status',events.length ? '' : covered ? 'No recent activity in the last 24 hours.' : listError ? 'Recent activity unavailable.' : 'Awaiting run history…')
  }

  function renderDeadlines(now) {
    const open = allRecords('tenders').filter(({item}) => item.status === 'open')
    const upcoming = open.filter(({item}) => deadlineTime(item.submissionDeadline) >= now).sort((a,b) => deadlineTime(a.item.submissionDeadline)-deadlineTime(b.item.submissionDeadline))
    const soon = upcoming.filter(({item}) => time(item.submissionDeadline) <= now + 7*day)
    const nearest = upcoming[0]?.item.submissionDeadline
    deadlineRecords = { open, soon, deadline: upcoming.filter(({item}) => deadlineTime(item.submissionDeadline) === deadlineTime(nearest)) }
    const covered = hasCoverage('tenders')
    text('snapshot-open',covered ? count(open.length) : '—')
    text('snapshot-soon',covered ? count(soon.length) : '—')
    text('snapshot-deadline',nearest ? deadlineDate(nearest) : covered ? 'None known' : '—')
    for (const button of $('snapshot-counts').querySelectorAll('button')) button.disabled = !deadlineRecords[button.dataset.deadline].length
    const unknown = open.filter(({item}) => !Number.isFinite(deadlineTime(item.submissionDeadline))).length
    text('snapshot-status',[coverage('tenders'),covered && !open.length ? 'No open tenders in the available data.' : '',unknown ? `${count(unknown)} open ${unknown === 1 ? 'tender lacks a usable deadline' : 'tenders lack usable deadlines'} and ${unknown === 1 ? 'is' : 'are'} excluded from deadline counts.` : '',open.some(({item}) => dateOnly(item.submissionDeadline)) ? 'Date-only deadlines use the UTC calendar day for comparisons.' : ''].filter(Boolean).join(' '))
    const affected = portals.filter(p => needsAttention(p) && data.tenders.get(String(p.id))?.items?.length).length
    $('snapshot-affected').hidden = !affected
    text('snapshot-affected',`Includes last available tenders from ${affected} affected ${affected === 1 ? 'portal' : 'portals'}.`)
  }

  function renderTender(tender, portal) {
    const identity = tender.id ?? tender.url ?? tender.referenceNumber ?? tender.title
    const fields = [
      ['Reference',escape(tender.referenceNumber ?? tender.reference ?? tender.id ?? '—')],
      ['Published',escape(deadlineDate(tender.publicationDate))],
      ['Last changed',escape(date(tender.lastChangedAt))],
      ['Procedure',escape(tender.procedureType ?? '—')],
    ]
    const address = tender.address ?? tender.url
    const base = safeUrl(portal.url)?.href
    const documents = (tender.documents ?? []).filter(Boolean).map(doc => {
      const source = safeUrl(doc.downloadPath ?? doc.path ?? doc.url)
      return source ? `<li><a href="${escape(source.href)}" download target="_blank" rel="noopener noreferrer">Download ${escape(doc.fileName ?? doc.filename ?? 'document')}</a></li>` : ''
    }).filter(Boolean)
    return `<article class="tender-card" data-key="${key(portal.id,identity)}"><h3>${escape(tender.title || 'Untitled tender')}</h3><div class="tender-overview"><span><small>Buyer</small>${escape(tender.buyerName ?? '—')}</span><span><small>Deadline</small>${escape(deadlineDate(tender.submissionDeadline))}</span><span><small>Status</small>${badge(tender.status ?? '',tender.status ?? '—')}</span></div>${address ? link(address,'Original notice ↗',base) : ''}<details><summary>Tender details</summary><dl class="tender-fields">${fields.map(([label,value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join('')}</dl>${tender.description ? `<p>${escape(tender.description)}</p>` : ''}${documents.length ? `<ul>${documents.join('')}</ul>` : '<small>No document records supplied.</small>'}</details></article>`
  }
  function renderRun(run, portalName = '') {
    const duration = time(run.finishedAt) - time(run.startedAt)
    return `<li class="entry" data-key="${key(run.id,portalName)}"><h3>${portalName ? `${escape(portalName)} · ` : ''}${runBadge(run)}</h3><small>Started ${escape(date(run.startedAt))}${run.finishedAt ? ` · Finished ${escape(date(run.finishedAt))}` : ''}${Number.isFinite(duration) && duration >= 0 ? ` · ${count(duration)} ms` : ''}</small>${run.detail ? `<p>${escape(run.detail)}</p>` : ''}</li>`
  }
  function renderDetail() {
    const p = portals.find(item => String(item.id) === selected)
    const supportsRefresh = p && capabilities.refresh
    const supportsRemoval = p && capabilities.remove
    $('controls').hidden = !supportsRefresh && !supportsRemoval
    $('check').hidden = !supportsRefresh
    $('remove').hidden = !supportsRemoval
    for (const id of ['portal-views','result-tools','tender-filters']) $(id).hidden = !p || (id !== 'portal-views' && tab !== 'tenders')
    if (!p) {
      $('view-portals').removeAttribute('data-detail')
      for (const id of ['repair-lifecycle','portal-usage','entries-error']) $(id).hidden = true
      markup('details',emptyDetail)
      markup('entries','')
      return
    }
    const history = data.runs.get(selected)
    const runs = [...history?.items ?? []].sort((a,b) => time(b.startedAt)-time(a.startedAt))
    const lastRead = runs.filter(run => run.kind === 'read' && run.result === 'ok').sort((a,b) => runStamp(b)-runStamp(a))[0]?.finishedAt
    const feed = data.tenders.get(selected)
    const snapshotStamp = p.lastSuccessAt || lastRead
    const latest = runs[0]
    markup('details',`<h2>${escape(nameOf(p))} ${portalBadge(p)}</h2><small id="portal-url">${link(p.url)}</small>${registration(p) ? `<p>Approximate registration location: ${escape(registration(p))}</p>` : ''}<p>${escape(p.reason || 'No status reason reported.')}</p><div class="portal-metadata"><small>Latest attempt · ${latest ? `${escape(runKind(latest))} ${escape(outcome(latest).toLowerCase())} · ${escape(date(latest.startedAt ?? latest.finishedAt))}` : history?.items ? 'None recorded' : history?.error ? 'Unavailable' : 'Loading…'}${history?.error && latest ? ` (cached ${escape(date(history.at))})` : ''}</small><small>Last successful read · ${lastRead ? escape(date(lastRead)) : history?.items ? 'None recorded' : '—'}</small></div>${needsAttention(p) && feed?.items?.length ? `<p class="repair-banner">Showing the last available tenders · ${snapshotStamp ? `last successful extraction ${escape(date(snapshotStamp))}` : `fetched ${escape(date(feed.at))}; extraction time unavailable`}</p>` : ''}`)
    $('tenders').setAttribute('aria-pressed',String(tab === 'tenders'))
    $('runs').setAttribute('aria-pressed',String(tab === 'runs'))
    $('check').disabled = checking.has(selected)
    text('check',checking.has(selected) ? 'Requesting…' : 'Refresh now')
    const entry = data[tab].get(selected)
    const stale = entry?.error || listError
    $('entries-error').hidden = !stale
    if (stale) text('entries-error',entry?.items ? `Could not refresh ${tab}. Showing results fetched ${date(entry.at)}.` : `${tab === 'runs' ? 'Run history' : 'Tenders'} unavailable.`)
    $('repair-lifecycle').hidden = !(tab === 'runs' && history?.items)
    let usageAvailable = false
    if (tab === 'runs' && history?.items) {
      const latestRepair = runs.find(run => run.kind === 'repair')
      markup('repair-lifecycle',`<h3>Repair activity</h3><p>${latestRepair ? `${runBadge(latestRepair)} · ${escape(date(runStamp(latestRepair)))}` : p.state === 'healing' ? 'Healing reported; no repair run logged yet.' : 'No repair recorded.'}</p>`)
      const totals = [['llmCalls','Model calls'],['inputTokens','Input tokens'],['outputTokens','Output tokens'],['estimatedCostUsd','Estimated cost (USD)']].flatMap(([field,label]) => {
        const values = runs.map(run => run.usage?.[field]).filter(Number.isFinite)
        return values.length ? [`${label} · ${count(values.reduce((sum,value) => sum+value,0))}`] : []
      })
      usageAvailable = totals.length > 0
      if (usageAvailable) markup('portal-usage',`<h3>Reported AI usage</h3><p>${totals.join(' · ')}</p>`)
    }
    $('portal-usage').hidden = !usageAvailable
    if (!entry?.items) { markup('entries',entry?.error ? '' : '<p>Loading records…</p>'); return }
    if (tab === 'runs') {
      markup('entries',runs.length ? `<ol class="timeline">${runs.map(run => renderRun(run)).join('')}</ol>` : '<p>No runs recorded yet.</p>')
      return
    }
    const search = $('tender-search').value.trim().toLowerCase()
    const status = $('tender-status').value
    const since = $('tender-since').value ? Date.now()-day : null
    const entries = entry.items.filter(tender => (!status || tender.status === status) && (!since || time(tender.lastChangedAt) > since) && [tender.title,tender.buyerName,tender.referenceNumber,tender.reference,tender.id].some(value => String(value ?? '').toLowerCase().includes(search)))
    text('filter-note',since ? 'Change window uses the last 24 hours.' : '')
    markup('entries',entries.map(tender => renderTender(tender,p)).join('') || `<p>${entry.items.length ? 'No tenders match these filters.' : 'No tenders available yet.'}</p>`)
  }

  async function discoverCapabilities() {
    if (Date.now()-capabilitiesAt < 300_000) return
    capabilitiesAt = Date.now()
    try {
      const { paths } = await request('/openapi.json')
      const routes = Object.entries(paths ?? {})
      capabilities = {
        refresh: routes.some(([url,methods]) => /^\/portals\/\{[^/]+\}\/refresh$/.test(url) && methods.post),
        remove: routes.some(([url,methods]) => /^\/portals\/\{[^/]+\}$/.test(url) && methods.delete),
      }
    } catch { capabilities = {} }
    renderDetail()
  }
  async function refreshRecords() {
    const now = Date.now()
    const prioritized = [...portals].sort((a,b) => Number(String(b.id) === selected)-Number(String(a.id) === selected))
    const queue = prioritized.flatMap(p => Object.keys(data).filter(kind => now - (data[kind].get(String(p.id))?.attempt ?? 0) >= intervals[kind]-500).map(kind => [p,kind]))
    let next = 0
    async function worker() {
      while (next < queue.length) {
        const [p,kind] = queue[next++]
        const id = String(p.id)
        const store = data[kind]
        const entry = { ...store.get(id), attempt:Date.now() }
        store.set(id,entry)
        try { entry.items = await list(`${path(p)}/${kind}`); entry.at = Date.now(); entry.error = false }
        catch { entry.error = true }
        if (selected === id) renderDetail()
      }
    }
    await Promise.all(Array.from({length:Math.min(queue.length,5)},worker))
  }
  function renderAll() {
    const now = Date.now()
    renderPortals()
    renderRuns(now)
    renderDeadlines(now)
    renderDetail()
    if (drilldown) renderDrilldown()
  }
  async function load() {
    if (loading) return
    loading = true
    const revision = mutation
    let retry = false
    try {
      let result
      try { result = await list('/portals'); listError = false } catch { listError = true }
      if (revision !== mutation) { retry = true; return }
      if (result) {
        // Keep inventory order stable across list refreshes, even if the API reorders it.
        const oldOrder = new Map(portals.map((p,i) => [String(p.id),i]))
        portals = result.filter(p => p?.id != null).sort((a,b) => (oldOrder.get(String(a.id)) ?? Infinity)-(oldOrder.get(String(b.id)) ?? Infinity))
        const ids = new Set(portals.map(p => String(p.id)))
        for (const store of Object.values(data)) for (const id of store.keys()) if (!ids.has(id)) store.delete(id)
        if (!ids.has(selected)) selected = null
        listAt = Date.now()
        void discoverCapabilities()
      }
      renderAll()
      if (result) { await refreshRecords(); renderAll() }
      retry = revision !== mutation
    } finally { loading = false; if (retry) void load() }
  }

  function renderDrilldown() {
    const {kind,id,title} = drilldown
    const records = kind === 'deadline' ? deadlineRecords[id] : id === 'active' ? activeRecords : chartRecords.get(id)
    text('record-title',title)
    text('record-note',coverage(kind === 'deadline' ? 'tenders' : 'runs'))
    if (!records?.length) { markup('record-list','<p>No matching records in the latest data.</p>'); return }
    const inner = kind === 'deadline' ? records.map(({portal,item}) => `<div data-key="${key(portal.id,item.id ?? item.url)}"><small>${escape(nameOf(portal))}</small>${renderTender(item,portal)}</div>`).join('')
      : `<ol class="timeline">${[...records].sort((a,b) => runStamp(b.item)-runStamp(a.item)).map(({portal,item}) => renderRun(item,nameOf(portal))).join('')}</ol>`
    markup('record-list',inner)
  }
  function openDrilldown(kind,id,title) {
    drilldown = {kind,id,title}
    renderDrilldown()
    $('record-dialog').showModal()
  }
  $('record-dialog').onclose = () => { drilldown = null }
  $('run-chart').onclick = event => {
    const button = event.target.closest('[data-slot]')
    if (button) openDrilldown('run',`${button.closest('.chart-day').dataset.stamp}:${button.dataset.slot}`,button.title)
  }
  $('active-runs').onclick = () => openDrilldown('run','active','Active reads and repairs')
  $('snapshot-counts').onclick = event => {
    const button = event.target.closest('[data-deadline]')
    if (button) openDrilldown('deadline',button.dataset.deadline,button.querySelector('span').textContent)
  }

  const views = { overview:'overview-title', portals:'portals-title', activity:'activity-view-title' }
  const nav = $('dashboard-nav')
  const portalView = $('view-portals')
  const narrow = matchMedia('(max-width: 660px)')
  let activeView

  function currentView() {
    const view = new URLSearchParams(location.search).get('view')
    return Object.hasOwn(views,view) ? view : 'overview'
  }
  function displayView(view, { focus = false, resetDetail = true } = {}) {
    const changed = activeView !== view
    const showingDetail = portalView.hasAttribute('data-detail')
    if (resetDetail) portalView.removeAttribute('data-detail')
    activeView = view
    for (const name of Object.keys(views)) $(`view-${name}`).hidden = name !== view
    for (const link of nav.querySelectorAll('[data-view]')) {
      if (link.dataset.view === view) link.setAttribute('aria-current','page')
      else link.removeAttribute('aria-current')
    }
    if (focus && (changed || (view === 'portals' && resetDetail && showingDetail))) {
      window.scrollTo({top:0,behavior:'instant'})
      $(views[view]).focus({preventScroll:true})
    }
  }
  function navigate(view, options) {
    if (currentView() !== view) {
      const url = new URL(location.href)
      if (view === 'overview') url.searchParams.delete('view')
      else url.searchParams.set('view',view)
      url.hash = ''
      history.pushState(null,'',url)
    }
    displayView(view,options)
  }
  nav.onclick = event => {
    const link = event.target.closest('[data-view]')
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    navigate(link.dataset.view,{focus:true})
  }
  history.scrollRestoration = 'manual'
  window.addEventListener('popstate',() => displayView(currentView(),{focus:true}))
  displayView(currentView())

  function setScope(status, openView = false) {
    scope = status
    $('portal-status').value = status
    if (openView) $('portal-search').value = ''
    renderPortals()
    if (openView) navigate('portals',{focus:true})
  }
  $('metrics').onclick = event => { const button = event.target.closest('[data-status]'); if (button) setScope(button.dataset.status,true) }
  $('attention-filter').onclick = () => setScope(scope === 'attention' ? '' : 'attention',true)
  $('portal-search').oninput = renderPortals
  $('portal-status').onchange = event => setScope(event.target.value)
  function showPortal(id, view) {
    const fromOutside = activeView !== 'portals'
    const changed = selected !== id
    selected = id
    tab = view
    if (changed) {
      for (const name of ['tender-search','tender-status','tender-since']) $(name).value = ''
      text('action-message','')
    }
    renderPortals()
    renderDetail()
    portalView.setAttribute('data-detail','')
    navigate('portals',{resetDetail:false})
    if (fromOutside || narrow.matches) {
      window.scrollTo({top:0,behavior:'instant'})
      $('portal-details-panel').focus({preventScroll:true})
    }
  }
  $('portal-back').onclick = () => {
    portalView.removeAttribute('data-detail')
    window.scrollTo({top:0,behavior:'instant'})
    const focusTarget = $('portals').querySelector('[aria-pressed=true]') || $('portal-list-title')
    focusTarget.focus({preventScroll:true})
  }
  for (const id of ['portal-picker','radar-view','recap-events','attention-portals']) $(id).onclick = event => {
    const button = event.target.closest('[data-portal]')
    if (button) showPortal(button.dataset.portal,id === 'recap-events' ? 'runs' : 'tenders')
  }
  $('radar-size').onclick = event => {
    const expanded = $('world-map').classList.toggle('is-expanded')
    text('zoom-label',expanded ? 'Zoom out' : 'Zoom in')
    event.currentTarget.setAttribute('aria-pressed',String(expanded))
  }
  for (const name of ['tenders','runs']) $(name).onclick = () => { if (tab !== name) { tab = name; renderDetail(); animate('entries') } }
  for (const id of ['tender-status','tender-since']) $(id).onchange = renderDetail
  $('tender-search').oninput = renderDetail

  $('add').onclick = () => { $('form').reset(); text('form-error',''); $('dialog').showModal() }
  $('cancel').onclick = () => $('dialog').close()
  $('form').onsubmit = async event => {
    event.preventDefault()
    text('form-error','')
    $('submit').disabled = true
    try {
      const input = $('url').value.trim()
      const url = input && safeUrl(/^[a-z][a-z\d+.-]*:\/\//i.test(input) ? input : `https://${input}`)
      if (!url) throw new Error('Enter a public HTTP or HTTPS URL.')
      const created = await request('/portals',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:url.href})})
      const createdPortal = await request(path(created)).catch(() => ({ id:created.id, url:url.href }))
      mutation++
      if (!portals.some(p => String(p.id) === String(created.id))) portals.push(createdPortal)
      $('dialog').close()
      setScope('',false)
      $('portal-search').value = ''
      showPortal(String(created.id),'tenders')
      $('portal-details-panel').focus({preventScroll:true})
      void load()
    } catch (error) { text('form-error',error.message) }
    finally { $('submit').disabled = false }
  }
  $('check').onclick = async () => {
    const p = portals.find(item => String(item.id) === selected)
    if (!p || !capabilities.refresh || checking.has(selected)) return
    const id = selected
    checking.add(id)
    renderDetail()
    try {
      await request(`${path(p)}/refresh`,{method:'POST'})
      if (selected === id) text('action-message','Refresh requested.')
      for (const store of Object.values(data)) if (store.has(id)) store.get(id).attempt = 0
      void load()
    } catch (error) { if (selected === id) text('action-message',`Could not request a refresh. ${error.message}`) }
    finally { checking.delete(id); renderDetail() }
  }
  $('remove').onclick = () => {
    const p = portals.find(item => String(item.id) === selected)
    if (!p || !capabilities.remove) return
    removal = p
    text('remove-description',`Stop monitoring ${nameOf(p)} and remove its tender and run history?`)
    text('remove-error','')
    $('remove-dialog').showModal()
  }
  $('remove-cancel').onclick = () => $('remove-dialog').close()
  $('remove-dialog').oncancel = event => { if ($('remove-confirm').disabled) event.preventDefault() }
  $('remove-form').onsubmit = async event => {
    event.preventDefault()
    if (!removal || !capabilities.remove || $('remove-confirm').disabled) return
    $('remove-confirm').disabled = $('remove-cancel').disabled = true
    try {
      await request(path(removal),{method:'DELETE'})
      mutation++
      portals = portals.filter(p => p.id !== removal.id)
      if (selected === String(removal.id)) selected = null
      $('remove-dialog').close()
      renderAll()
      $('portal-list-title').focus({preventScroll:true})
      void load()
    } catch (error) { text('remove-error',error.message) }
    finally { $('remove-confirm').disabled = $('remove-cancel').disabled = false }
  }

  initChart()
  setInterval(load,10_000)
  void load()
})()
