import { ArrowRight, CalendarDays, ChartNoAxesColumn, CircleAlert, CircleCheck, Globe2, Layers3, Plug, Wrench } from "lucide-react";

const states = [
  ["", "Monitored", "All tracked portals", Layers3],
  ["serving", "Serving", "Delivering tenders", CircleCheck],
  ["integrating", "Integrating", "Setting up extraction", Plug],
  ["healing", "Healing", "Repair in progress", Wrench],
  ["failed", "Failed", "Operator review needed", CircleAlert],
];

function EuropeRadar() {
  return (
    <section className="panel radar-panel" aria-labelledby="radar-title">
      <div className="panel-heading">
        <Globe2 className="section-icon" size={17} aria-hidden="true" />
        <div><h2 id="radar-title">Europe radar</h2><p>Portal registration locations</p></div>
        <button type="button" id="radar-size" aria-controls="radar-viewport" aria-pressed="false"><span id="zoom-label">Zoom in</span></button>
      </div>
      <div className="radar" id="radar-view" role="group" aria-label="Europe radar" aria-describedby="map-note">
        <div className="radar-viewport" id="radar-viewport">
          <div className="world-map" id="world-map">
            <div className="world-geography" aria-hidden="true" />
            <div className="world-markers" id="world-markers" />
          </div>
          <span className="map-tag" aria-hidden="true">EUROPE / REGISTRATION</span>
        </div>
        <details id="unlocated-portals" hidden>
          <summary>Unknown or outside map <span id="unlocated-count" /></summary>
          <ul className="unlocated-buttons" id="unlocated-buttons" />
        </details>
        <p id="radar-empty" role="status">Loading portal locations…</p>
      </div>
      <div className="radar-key">
        <div className="radar-legend">
          {states.slice(1).map(([state, label]) => <span key={state}><i className={`legend-dot signal-${state}`} aria-hidden="true" />{label}</span>)}
        </div>
        <small id="map-note">Locations reflect domain registration or a country estimate, not the tender location.</small>
      </div>
    </section>
  );
}

function Activity() {
  return (
    <section className="panel activity-panel" aria-labelledby="activity-title">
      <div className="panel-heading">
        <ChartNoAxesColumn className="section-icon" size={17} aria-hidden="true" />
        <div><h2 id="activity-title">Run activity</h2><p>Reads and repairs · 7 days</p></div>
        <button type="button" id="active-runs" className="active-runs" disabled>— active</button>
      </div>
      <div className="chart-summary">
        <div><strong id="success-rate">—</strong><span>Success rate</span></div>
        <small id="run-denominator">Loading completed runs…</small>
      </div>
      <div className="chart-plot" id="run-chart" role="group" aria-label="Completed runs by day, kind and outcome" />
      <div className="chart-legend"><span><i className="legend-dot signal-serving" />Succeeded</span><span><i className="legend-dot signal-failed" />Failed</span><span>Read / repair · UTC</span></div>
      <p id="chart-coverage" className="data-note" role="status" />
      <div className="activity-recap">
        <div className="row"><h3>Recent activity</h3><small>Last 24 hours · UTC</small></div>
        <p id="recap-status" role="status">Loading recent events…</p>
        <ol id="recap-events" className="recap-events" />
      </div>
    </section>
  );
}

export default function OperationsOverview() {
  return (
    <>
      <section className="overview" aria-labelledby="overview-title">
        <div className="overview-heading">
          <div className="overview-kicker">
            <p className="eyebrow">Procurement monitoring</p>
            <span id="sync-status" className="sync-status" role="status"><i aria-hidden="true" /> <span id="sync-label">Connecting to API</span></span>
          </div>
          <h1 id="overview-title">Operations overview</h1>
          <p>Track portal health, automated repairs, and upcoming tender deadlines.</p>
          <p id="message" role="status" />
        </div>
        <div className="metrics" id="metrics" role="group" aria-label="Filter portals by health">
          {states.map(([state, label, description, MetricIcon]) => (
            <button type="button" className={`metric signal-${state || "unknown"}`} data-status={state} key={state} aria-pressed={!state} aria-controls="portal-picker" title={description}>
              <span className="metric-label"><MetricIcon size={15} aria-hidden="true" /><span>{label}</span></span><strong>—</strong>
            </button>
          ))}
        </div>
        <section className="attention panel" aria-labelledby="attention-title">
          <div className="panel-heading">
            <CircleAlert className="section-icon warning" size={17} aria-hidden="true" />
            <div><h2 id="attention-title">Needs attention <span id="attention-count" className="count-tag">—</span></h2><p id="attention-summary" role="status">Checking portal health…</p></div>
            <button type="button" id="attention-filter" aria-pressed="false" aria-controls="portal-picker">View affected <ArrowRight size={14} aria-hidden="true" /></button>
          </div>
          <ul id="attention-portals" className="attention-portals" />
        </section>
      </section>
      <div className="overview-grid"><EuropeRadar /><Activity /></div>
      <section className="panel tender-snapshot" aria-labelledby="snapshot-title">
        <div className="panel-heading"><CalendarDays className="section-icon" size={17} aria-hidden="true" /><div><h2 id="snapshot-title">Tender deadlines</h2><p>Upcoming submissions across monitored portals · UTC</p></div></div>
        <div className="snapshot-counts" id="snapshot-counts">
          {[["open", "Open tenders"], ["soon", "Closing in 7 days"], ["deadline", "Next known deadline"]].map(([kind, title]) => <button type="button" key={kind} data-deadline={kind} disabled><span>{title}</span><strong id={`snapshot-${kind}`}>—</strong><ArrowRight size={15} aria-hidden="true" /></button>)}
        </div>
        <p id="snapshot-affected" className="data-note" hidden />
        <p id="snapshot-status" className="data-note" role="status">Loading tender data…</p>
      </section>
    </>
  );
}
