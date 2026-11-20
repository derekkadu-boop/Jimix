import Script from "next/script";
import ThemeToggle from "./theme-toggle";
import AgentSettings, { AgentCostSummary } from "./agent-settings";

export default function Dashboard() {
  return (
    <>
      <header>
        <div>
          <div className="header-brand"><strong><svg className="patterno-mark" width="28" height="28" aria-hidden="true" focusable="false" viewBox="0 0 800 800" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M430.722 380.158C276.637 380.158 142.935 292.957 76.1625 165.282C31.9187 226.23 4.37923 300.09 0.496614 380.158C0.158014 386.749 0 393.341 0 400C0 406.659 0.158014 413.251 0.496614 419.842C4.37923 499.91 31.9187 573.77 76.1851 634.718C142.957 507.065 276.659 419.842 430.745 419.842H799.436C799.819 413.296 800 406.659 800 400C800 393.341 799.842 386.704 799.436 380.158H430.722Z" fill="currentColor"></path><path d="M602.957 472.619C599.887 472.619 596.84 472.731 593.792 472.799C417.02 476.772 268.555 595.305 219.91 757.178C274.041 784.56 335.214 799.955 399.977 799.955C579.278 799.955 731.016 681.941 781.738 519.3H781.851C782.235 518.081 782.573 516.84 782.935 515.621C782.957 515.531 782.98 515.463 783.025 515.372C787.201 501.422 790.61 487.133 793.273 472.596H602.935L602.957 472.619Z" fill="currentColor"></path><path d="M565.508 325.553C577.833 326.704 590.316 327.336 602.957 327.336H793.296C790.632 312.799 787.224 298.51 783.025 284.56C782.957 284.357 782.889 284.176 782.844 283.973C782.777 283.747 782.732 283.544 782.664 283.318H782.641C732.754 119.3 580.271 0 400 0C335.237 0 274.063 15.395 219.932 42.7765C265.959 195.937 401.354 310.316 565.531 325.553H565.508Z" fill="currentColor"></path></svg><span className="brand-wordmark">Watchtower</span></strong><small className="demo-label">Demo view</small></div>
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
        <section className="panel radar-panel">
          <div className="row radar-panel-heading">
            <h2>Europe radar</h2>
            <button type="button" id="radar-size" aria-controls="radar-viewport" aria-pressed="false">Zoom in</button>
          </div>
          <div className="radar-panel-body">
            <div className="radar" id="radar-view" role="group" aria-label="Europe radar">
              <div className="radar-viewport" id="radar-viewport">
                <div className="world-map" id="world-map">
                  <div className="world-geography" aria-hidden="true"></div>
                  <div className="world-markers" id="world-markers"></div>
                </div>
              </div>
              <div className="unlocated-portals" id="unlocated-portals" hidden>
                <small>Outside the map or location unknown</small>
                <div className="unlocated-buttons" id="unlocated-buttons"></div>
              </div>
              <p className="radar-empty" id="radar-empty" hidden></p>
            </div>
            <section className="tender-snapshot" aria-labelledby="snapshot-title">
              <h3 id="snapshot-title">Tender snapshot</h3>
              <dl className="snapshot-counts">
                <div><dt>Open tenders</dt><dd id="snapshot-open">—</dd></div>
                <div><dt>Closing in 7 days</dt><dd id="snapshot-soon">—</dd></div>
                <div><dt>Closed</dt><dd id="snapshot-closed">—</dd></div>
                <div><dt>Withdrawn</dt><dd id="snapshot-withdrawn">—</dd></div>
              </dl>
              <p id="snapshot-deadline" hidden></p>
              <p id="snapshot-affected" hidden></p>
              <p id="snapshot-status" role="status">Loading tenders…</p>
              <button type="button" id="browse-open" hidden>Browse open tenders</button>
            </section>
          </div>
          <div className="radar-key">
              <small className="radar-legend" id="radar-legend">
                <span><strong><i className="legend-dot signal-integrating" aria-hidden="true"></i>integrating</strong><span>Being attached. Nothing is delivered yet.</span></span>
                <span><strong><i className="legend-dot signal-serving" aria-hidden="true"></i>serving</strong><span>Delivering correctly.</span></span>
                <span><strong><i className="legend-dot signal-healing" aria-hidden="true"></i>healing</strong><span>Broken. A repair is running.</span></span>
                <span><strong><i className="legend-dot signal-failed" aria-hidden="true"></i>failed</strong><span>Cannot deliver. A person is needed.</span></span>
              </small>
              <small id="map-note">Markers use domain registration locations when available. Country markers are approximate.</small>
          </div>
        </section>
        <div className="layout">
          <aside className="panel sidebar" aria-labelledby="portal-list-title">
            <h2 id="portal-list-title">Monitored portals</h2>
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
              <div className="list" id="portals" role="group" aria-label="Monitored portals"></div>
            </div>
          </aside>
          <section className="panel" id="portal-details-panel" aria-label="Portal details">
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
