import Script from "next/script";
import ThemeToggle from "./theme-toggle";
import AgentSettings, { AgentCostSummary } from "./agent-settings";

export default function Dashboard() {
  return (
    <>
      <header>
        <div>
          <div className="header-brand"><strong><span className="brand-mark" aria-hidden="true">◈</span>Watchtower</strong><small className="demo-label">Demo view</small></div>
          <AgentCostSummary />
          <div className="header-actions">
            <button className="primary" id="add">+ Add portal</button>
            <ThemeToggle />
            <AgentSettings />
          </div>
        </div>
      </header>
      <main>
        <section className="overview" aria-labelledby="overview-title">
          <h1 id="overview-title">Portal <span>overview</span></h1>
          <p>Monitor public tender portals and review their latest runs.</p>
          <div className="metrics" id="metrics"></div>
        </section>
        <p id="message" role="status"></p>
        <section className="panel morning-recap" aria-labelledby="recap-title">
          <h2 id="recap-title">What happened?</h2>
          <small>Live activity · Last 24 hours · Updates every 10 seconds</small>
          <p id="recap-status" role="status">Loading recent runs…</p>
          <ol id="recap-events" className="recap-events"></ol>
        </section>
        <div className="layout">
          <aside className="panel sidebar" aria-labelledby="portal-list-title">
            <h2 id="portal-list-title">Monitored portals</h2>
            <div className="view-switch" id="portal-view-switch" role="group" aria-label="Portal view">
              <button id="view-radar" type="button" aria-pressed="true">Radar</button>
              <button id="view-list" type="button" aria-pressed="false">List</button>
            </div>
            <label className="portal-search" htmlFor="portal-search">Search portals
              <input id="portal-search" type="search" placeholder="Search by name or URL" autoComplete="off" aria-controls="portal-picker" />
            </label>
            <label className="portal-search" htmlFor="portal-status">Portal status
              <select id="portal-status" defaultValue="" aria-controls="portal-picker">
                <option value="">All statuses</option>
                <option value="failed">failed</option>
                <option value="healing">healing</option>
                <option value="integrating">integrating</option>
                <option value="serving">serving</option>
              </select>
            </label>
            <div id="portal-picker">
              <div className="list" id="portals" role="group" aria-label="Monitored portals" hidden></div>
              <div className="radar" id="radar-view" role="group" aria-label="Europe radar">
                <div className="world-map">
                  <div className="world-geography" aria-hidden="true"></div>
                  <div className="world-markers" id="world-markers"></div>
                </div>
                <div className="unlocated-portals" id="unlocated-portals" hidden>
                  <small>Outside the map or location unknown</small>
                  <div className="unlocated-buttons" id="unlocated-buttons"></div>
                </div>
                <p className="radar-empty" id="radar-empty" hidden></p>
              </div>
            </div>
            <small className="radar-legend" id="radar-legend">
              <span><strong><i className="legend-dot signal-integrating" aria-hidden="true"></i>integrating</strong><span>Being attached. Nothing is delivered yet.</span></span>
              <span><strong><i className="legend-dot signal-serving" aria-hidden="true"></i>serving</strong><span>Delivering correctly.</span></span>
              <span><strong><i className="legend-dot signal-healing" aria-hidden="true"></i>healing</strong><span>Broken. A repair is running.</span></span>
              <span><strong><i className="legend-dot signal-failed" aria-hidden="true"></i>failed</strong><span>Cannot deliver. A person is needed.</span></span>
            </small>
            <small id="map-note">Markers use domain registration locations when available. Country markers are approximate.</small>
          </aside>
          <section className="panel" aria-label="Portal details">
            <div className="portal-detail-top">
              <div id="details"><p>Select a portal to see its details.</p></div>
              <div className="portal-actions" id="controls" role="group" aria-label="Selected portal actions" hidden>
                <button id="check">Refresh now</button><button id="remove" className="danger">Remove portal</button>
              </div>
            </div>
            <p id="action-message" role="status"></p>
            <div className="portal-views" id="portal-views" role="group" aria-label="Selected portal results" hidden>
              <button id="tenders" aria-pressed="true">Tenders</button><button id="runs" aria-pressed="false">Run history</button>
            </div>
            <div className="result-tools" id="result-tools" hidden>
              <div id="tender-filters" className="actions" hidden>
                <label>Changed <select id="tender-since" defaultValue=""><option value="">All dates</option><option value="24h">Last 24 hours</option></select></label>
                <label>Status <select id="tender-status" defaultValue=""><option value="">All statuses</option><option value="open">Open</option><option value="closed">Closed</option><option value="withdrawn">Withdrawn</option></select></label>
                <label>Search tenders <input id="tender-search" type="search" placeholder="Title, buyer or reference" /></label>
                <small id="filter-note"></small>
              </div>
            </div>
            <div id="repair-lifecycle" hidden></div>
            <div id="portal-usage" hidden></div>
            <p id="entries-error" role="status" hidden></p>
            <div id="entries"></div>
          </section>
        </div>
      </main>
      <dialog id="dialog">
        <form id="form"><h2>Add portal</h2><p>Enter the public address of a tender portal.</p><label htmlFor="url">Portal URL</label><input id="url" required type="text" inputMode="url" autoCapitalize="none" spellCheck="false" placeholder="procurement.example" /><p id="form-error" role="alert"></p><div className="actions"><button type="button" id="cancel">Cancel</button><button className="primary" id="submit">Add portal</button></div></form>
      </dialog>
      <dialog id="remove-dialog" aria-labelledby="remove-title" aria-describedby="remove-description">
        <form id="remove-form">
          <h2 id="remove-title">Remove portal?</h2>
          <p id="remove-description"></p>
          <p id="remove-error" role="alert"></p>
          <div className="actions"><button type="button" id="remove-cancel" autoFocus>Cancel</button><button className="danger" id="remove-confirm">Remove portal</button></div>
        </form>
      </dialog>
      <Script src="/dashboard.js" strategy="afterInteractive" />
    </>
  );
}
