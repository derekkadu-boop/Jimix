import Script from "next/script";
import ThemeToggle from "./theme-toggle";
import AgentSettings, { AgentCostSummary } from "./agent-settings";
import { ArrowLeft, Inbox, Plus, Trash2 } from "lucide-react";
import OperationsOverview, { Activity } from "./operations-overview";

export default function Dashboard() {
  return (
    <>
      <header>
        <div>
          <div className="header-brand"><strong><svg className="patterno-mark" width="28" height="28" aria-hidden="true" focusable="false" viewBox="0 0 800 800" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M430.722 380.158C276.637 380.158 142.935 292.957 76.1625 165.282C31.9187 226.23 4.37923 300.09 0.496614 380.158C0.158014 386.749 0 393.341 0 400C0 406.659 0.158014 413.251 0.496614 419.842C4.37923 499.91 31.9187 573.77 76.1851 634.718C142.957 507.065 276.659 419.842 430.745 419.842H799.436C799.819 413.296 800 406.659 800 400C800 393.341 799.842 386.704 799.436 380.158H430.722Z" fill="currentColor"></path><path d="M602.957 472.619C599.887 472.619 596.84 472.731 593.792 472.799C417.02 476.772 268.555 595.305 219.91 757.178C274.041 784.56 335.214 799.955 399.977 799.955C579.278 799.955 731.016 681.941 781.738 519.3H781.851C782.235 518.081 782.573 516.84 782.935 515.621C782.957 515.531 782.98 515.463 783.025 515.372C787.201 501.422 790.61 487.133 793.273 472.596H602.935L602.957 472.619Z" fill="currentColor"></path><path d="M565.508 325.553C577.833 326.704 590.316 327.336 602.957 327.336H793.296C790.632 312.799 787.224 298.51 783.025 284.56C782.957 284.357 782.889 284.176 782.844 283.973C782.777 283.747 782.732 283.544 782.664 283.318H782.641C732.754 119.3 580.271 0 400 0C335.237 0 274.063 15.395 219.932 42.7765C265.959 195.937 401.354 310.316 565.531 325.553H565.508Z" fill="currentColor"></path></svg><span className="brand-wordmark">Watchtower</span></strong></div>
          <nav id="dashboard-nav" aria-label="Dashboard sections">
            <a href="/" data-view="overview" aria-current="page">Overview</a>
            <a href="?view=portals" data-view="portals">Portals</a>
            <a href="?view=activity" data-view="activity">Activity</a>
          </nav>
          <div className="header-actions">
            <ThemeToggle />
            <AgentSettings />
            <button className="primary" id="add" type="button"><Plus size={16} aria-hidden="true" /> Add portal</button>
          </div>
        </div>
      </header>
      <main>
        <div id="view-overview"><OperationsOverview /></div>
        <section id="view-portals" aria-labelledby="portals-title" hidden>
          <div className="view-heading"><p className="eyebrow">Portal inventory</p><h1 id="portals-title" tabIndex={-1}>Portals</h1><p>Find a monitored portal and review its tenders or run history.</p></div>
          <button type="button" id="portal-back"><ArrowLeft size={16} aria-hidden="true" /> Back to portals</button>
          <div className="layout">
          <aside className="panel sidebar" aria-labelledby="portal-list-title">
            <h2 id="portal-list-title" tabIndex={-1}>Monitored portals</h2>
            <p id="portal-list-message" className="data-note" role="status"></p>
            <label className="portal-search" htmlFor="portal-search">Search portals
              <input id="portal-search" type="search" placeholder="Search by name or URL" autoComplete="off" aria-controls="portal-picker" />
            </label>
            <label className="portal-search" htmlFor="portal-status">Portal status
              <select id="portal-status" defaultValue="" aria-controls="portal-picker">
                <option value="">All statuses</option>
                <option value="attention">Needs attention</option>
                <option value="failed">Failed</option>
                <option value="healing">Healing</option>
                <option value="integrating">Integrating</option>
                <option value="serving">Serving</option>
              </select>
            </label>
            <div id="portal-picker">
              <div className="list" id="portals" role="group" aria-label="Monitored portals"></div>
            </div>
          </aside>
          <section className="panel" id="portal-details-panel" aria-label="Portal details" tabIndex={-1}>
            <div className="portal-detail-top">
              <div id="details"><div className="detail-empty"><Inbox size={22} aria-hidden="true" /><h2>Select a portal</h2><p>Choose a portal to review tenders and run history.</p></div></div>
              <div className="portal-actions" id="controls" role="group" aria-label="Selected portal actions" hidden>
                <button id="check" hidden>Refresh now</button><button type="button" id="remove" className="danger delete-action" hidden><Trash2 size={15} aria-hidden="true" /> Delete portal</button>
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
        </section>
        <section id="view-activity" aria-labelledby="activity-view-title" hidden>
          <div className="view-heading"><p className="eyebrow">Run monitoring</p><h1 id="activity-view-title" tabIndex={-1}>Activity</h1><p>Track read and repair runs, outcomes, and recent events.</p></div>
          <Activity />
        </section>
        <footer><small>Patterno Watchtower</small><AgentCostSummary /></footer>
      </main>
      <dialog id="record-dialog" aria-labelledby="record-title">
        <div className="row record-head"><h2 id="record-title">Records</h2><form method="dialog"><button aria-label="Close records">Close</button></form></div>
        <p id="record-note" />
        <div id="record-list" />
      </dialog>
      <dialog id="dialog" aria-labelledby="add-title">
        <form id="form"><h2 id="add-title">Add portal</h2><p>Enter the public address of a tender portal.</p><label htmlFor="url">Portal URL</label><input id="url" required type="text" inputMode="url" autoCapitalize="none" spellCheck="false" placeholder="procurement.example" /><p id="form-error" role="alert"></p><div className="actions"><button type="button" id="cancel">Cancel</button><button className="primary" id="submit">Add portal</button></div></form>
      </dialog>
      <dialog id="remove-dialog" aria-labelledby="remove-title" aria-describedby="remove-description">
        <form id="remove-form">
          <h2 id="remove-title">Delete portal?</h2>
          <p id="remove-description"></p>
          <p id="remove-error" role="alert"></p>
          <div className="actions"><button type="button" id="remove-cancel" autoFocus>Cancel</button><button type="submit" className="danger delete-action" id="remove-confirm">Delete portal</button></div>
        </form>
      </dialog>
      <Script src="/dashboard.js" strategy="afterInteractive" />
    </>
  );
}
