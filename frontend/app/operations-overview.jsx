const states = [
  ["", "Monitored", "All tracked portals", "grid"],
  ["serving", "Serving", "Delivering tenders", "check"],
  ["integrating", "Integrating", "Setting up extraction", "plus"],
  ["healing", "Healing", "Repair in progress", "pulse"],
  ["failed", "Failed", "Operator review needed", "alert"],
];

export function Icon({ name, size = 18 }) {
  const paths = {
    grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
    check: "M22 11.1V12a10 10 0 1 1-5.9-9.1M22 4 12 14l-3-3",
    plus: "M12 5v14M5 12h14",
    pulse: "M2 12h5l3-8 4 16 3-8h5",
    alert: "M12 8v4m0 4h.01M10.3 4.4 2.8 18a1.5 1.5 0 0 0 1.3 2.2h15.8a1.5 1.5 0 0 0 1.3-2.2L13.7 4.4a2 2 0 0 0-3.4 0Z",
    arrow: "M4 12h15m-6-6 6 6-6 6",
    radar: "M12 12l8-8M20.6 8a10 10 0 1 1-4.6-4.6M17.7 10a6 6 0 1 1-3.7-3.7M12 12h.01",
    calendar: "M8 2v4m8-4v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z",
    inbox: "M4 4h16l2 11v5H2v-5L4 4Zm-2 11h6l2 2h4l2-2h6",
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}

function EuropeRadar() {
  return (
    <section className="panel radar-panel" aria-labelledby="radar-title">
      <div className="panel-heading">
        <div className="section-icon"><Icon name="radar" /></div>
        <div><h2 id="radar-title">Europe radar</h2><p>Approximate portal registration locations</p></div>
        <button type="button" id="radar-size" aria-controls="radar-viewport" aria-pressed="false"><span aria-hidden="true">+</span> <span id="zoom-label">Zoom in</span></button>
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
        <div className="section-icon"><Icon name="pulse" /></div>
        <div><h2 id="activity-title">Run activity</h2><p>Reads and repairs · last 7 days (UTC)</p></div>
        <button type="button" id="active-runs" className="active-runs" disabled>— active</button>
      </div>
      <div className="chart-summary">
        <div><strong id="success-rate">—</strong><span>Success rate</span></div>
        <small id="run-denominator">Loading completed runs…</small>
      </div>
      <div className="chart-plot" id="run-chart" role="group" aria-label="Completed runs by day, kind and outcome" />
      <div className="chart-legend"><span><i className="legend-dot signal-serving" />Succeeded</span><span><i className="legend-dot signal-failed" />Failed</span><span>Each day: read / repair</span></div>
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
          <div><p className="eyebrow"><span /> PROCUREMENT MONITORING</p><h1 id="overview-title">Operations overview</h1><p>Track portal health, automated repairs, and upcoming tender deadlines.</p></div>
          <span id="sync-status" className="sync-status" role="status"><i aria-hidden="true" /> <span id="sync-label">Connecting to API</span></span>
        </div>
        <p id="message" role="status" />
        <section className="attention panel" aria-labelledby="attention-title">
          <div className="panel-heading">
            <div className="section-icon warning"><Icon name="alert" /></div>
            <div><h2 id="attention-title">Needs attention <span id="attention-count" className="count-tag">—</span></h2><p id="attention-summary" role="status">Checking portal health…</p></div>
            <button type="button" id="attention-filter" aria-pressed="false" aria-controls="portal-picker">View affected <Icon name="arrow" size={15} /></button>
          </div>
          <ul id="attention-portals" className="attention-portals" />
        </section>
        <div className="metrics" id="metrics" role="group" aria-label="Filter portals by health">
          {states.map(([state, label, description, icon]) => (
            <button type="button" className={`metric signal-${state || "unknown"}`} data-status={state} key={state} aria-pressed={!state} aria-controls="portal-picker">
              <span className="metric-label"><Icon name={icon} size={15} />{label}</span><strong>—</strong><small>{description}</small>
            </button>
          ))}
        </div>
      </section>
      <div className="overview-grid"><EuropeRadar /><Activity /></div>
      <section className="panel tender-snapshot" aria-labelledby="snapshot-title">
        <div className="panel-heading"><div className="section-icon"><Icon name="calendar" /></div><div><h2 id="snapshot-title">Tender deadlines</h2><p>Upcoming submissions across monitored portals · UTC</p></div></div>
        <div className="snapshot-counts" id="snapshot-counts">
          {[["open", "Open tenders"], ["soon", "Closing in 7 days"], ["deadline", "Next known deadline"]].map(([kind, title]) => <button type="button" key={kind} data-deadline={kind} disabled><span>{title}</span><strong id={`snapshot-${kind}`}>—</strong><Icon name="arrow" size={15} /></button>)}
        </div>
        <p id="snapshot-affected" className="data-note" hidden />
        <p id="snapshot-status" className="data-note" role="status">Loading tender data…</p>
      </section>
    </>
  );
}
