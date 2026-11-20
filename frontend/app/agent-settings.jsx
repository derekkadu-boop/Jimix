"use client";

import { useEffect, useRef, useState } from "react";

const storageKey = "watchtower.agentSettings";
const limits = [
  ["fetches", "Fetches per run"],
  ["seconds", "Time limit (seconds)"],
  ["llmCalls", "LLM calls per run"],
  ["totalTokens", "Total tokens per run"],
  ["inputTokens", "Input tokens per run"],
  ["outputTokens", "Output tokens per run"],
];
// OpenAI Standard short-context rates in USD per million tokens, checked 2026-11-20.
const models = {
  "gpt-6.2-sol": { label: "GPT-6.2 Sol", input: 2, output: 10, context: 1_050_000, longContext: true },
  "gpt-6.2-astra": { label: "GPT-6.2 Astra", input: 8, output: 40, context: 1_050_000, longContext: true },
  "gpt-6-astra": { label: "GPT-6 Astra", input: 10, output: 50, context: 1_050_000, longContext: true },
  "gpt-6.1-sol": { label: "GPT-6.1 Sol", input: 2, output: 10, context: 1_050_000, longContext: true },
  "gpt-6.1-terra": { label: "GPT-6.1 Terra", input: 1, output: 5, context: 1_050_000, longContext: true },
  "gpt-6-luna": { label: "GPT-6 Luna", input: 0.10, output: 0.50, context: 1_050_000, longContext: true },
  "gpt-5.4": { label: "GPT-5.4", input: 2.50, output: 15, context: 1_050_000, longContext: true },
  "gpt-5.4-mini": { label: "GPT-5.4 mini", input: 0.75, output: 4.50, context: 400_000 },
  "gpt-5.4-nano": { label: "GPT-5.4 nano", input: 0.20, output: 1.25, context: 400_000 },
};
const defaultModel = "gpt-6.2-sol";

const maxOutputPerCall = 128_000;
const estimatePrompt = "Set a total token limit, an LLM call limit, or both input and output token limits in Settings.";

function estimateCost(values, model) {
  const calls = values.llmCalls ? Number(values.llmCalls) : Infinity;
  const tokenCap = values.totalTokens ? Number(values.totalTokens) : Infinity;
  const inputCap = values.inputTokens ? Number(values.inputTokens) : Infinity;
  const outputCap = values.outputTokens ? Number(values.outputTokens) : Infinity;
  if ([calls, tokenCap, inputCap, outputCap].some(limit => limit < 0)) return null;
  if (calls === 0 || tokenCap === 0) return 0;

  const maxOutput = Math.min(outputCap, calls * maxOutputPerCall);
  const tokens = Math.min(tokenCap, calls * model.context, inputCap + maxOutput);
  const output = Math.min(tokens, maxOutput);
  // Apply the higher rates conservatively when the run budget allows a long prompt.
  const longContext = model.longContext && Math.min(tokens, inputCap) > 272_000;
  const inputRate = model.input * (longContext ? 2 : 1);
  const outputRate = model.output * (longContext ? 1.5 : 1);
  // The long-context tier needs room for a prompt above its threshold.
  const longOutput = Math.min(output, tokens - 272_001);
  const standardCost = (tokens - output) * model.input + output * model.output;
  const longCost = longContext ? (tokens - longOutput) * inputRate + longOutput * outputRate : 0;
  const estimate = Math.max(standardCost, longCost) / 1_000_000;
  return Number.isFinite(estimate) ? estimate : null;
}

function formatCost(estimate) {
  return estimate.toLocaleString("en-US", {
    style: "currency", currency: "USD", maximumFractionDigits: 6,
  });
}

export function AgentCostSummary() {
  const [saved, setSaved] = useState({});

  useEffect(() => {
    function readSettings() {
      try {
        setSaved(JSON.parse(localStorage.getItem(storageKey)) ?? {});
      } catch {
        setSaved({});
      }
    }
    readSettings();
    window.addEventListener(storageKey, readSettings);
    window.addEventListener("storage", readSettings);
    return () => {
      window.removeEventListener(storageKey, readSettings);
      window.removeEventListener("storage", readSettings);
    };
  }, []);

  const rates = models[Object.hasOwn(models, saved.model) ? saved.model : defaultModel];
  const estimate = estimateCost(saved, rates);
  return (
    <div className="agent-cost-summary" aria-label="Agent run cost">
      <small>Est. per run · {rates.label} ·</small>
      <output aria-live="polite">
        {estimate === null ? "Set limits" : `Up to ${formatCost(estimate)} USD`}
      </output>
    </div>
  );
}

export default function AgentSettings() {
  const dialog = useRef(null);
  const [values, setValues] = useState({});
  const [error, setError] = useState("");

  function openSettings() {
    setError("");
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey)) ?? {};
      const limitsOnly = Object.fromEntries(limits.map(([key]) => [key, saved[key] ?? ""]));
      setValues({ ...limitsOnly, model: saved.model });
    } catch {
      setValues({});
    }
    dialog.current.showModal();
  }

  function saveSettings(event) {
    event.preventDefault();
    try {
      localStorage.setItem(storageKey, JSON.stringify({ ...values, model }));
      window.dispatchEvent(new Event(storageKey));
      dialog.current.close();
    } catch {
      setError("Could not save settings in this browser.");
    }
  }

  function field([key, label]) {
    return (
      <label key={key}>
        {label}
        <input
          type="number"
          min="0"
          step="1"
          value={values[key] ?? ""}
          onChange={event => {
            const value = event.target.value;
            setValues(previous => ({ ...previous, [key]: value }));
          }}
        />
      </label>
    );
  }

  const model = Object.hasOwn(models, values.model) ? values.model : defaultModel;
  const rates = models[model];
  const estimate = estimateCost(values, rates);

  return (
    <>
      <button className="settings-toggle" type="button" aria-label="Settings" title="Settings" onClick={openSettings}>
        <span aria-hidden="true">⚙</span>
      </button>
      <dialog ref={dialog} className="agent-settings" aria-labelledby="agent-settings-title">
        <form onSubmit={saveSettings}>
          <h2 id="agent-settings-title">Agent settings</h2>
          <p>Saved in this browser for planning. The backend currently has no controls to enforce these limits.</p>
          <fieldset>
            <legend>Run limits</legend>
            <div className="settings-grid">{limits.map(field)}</div>
          </fieldset>
          <fieldset>
            <legend>Model and estimated cost</legend>
            <label className="settings-model">
              <span>Model</span>
              <select
                value={model}
                onChange={event => setValues({ ...values, model: event.target.value })}
              >
                {Object.entries(models).map(([id, entry]) => <option key={id} value={id}>{entry.label}</option>)}
              </select>
            </label>
            <p>${rates.input.toFixed(2)} input and ${rates.output.toFixed(2)} output per million tokens (USD).</p>
            <p>{rates.context.toLocaleString("en-US")} context tokens and {maxOutputPerCall.toLocaleString("en-US")} maximum output tokens per call.</p>
            <p>Estimate uses uncached token rates, including a conservative long-context allowance where applicable. Tool charges are excluded. <a href="https://developers.openai.com/api/docs/pricing">OpenAI pricing</a></p>
            <p>
              <output aria-live="polite">
                {estimate !== null
                  ? `Estimated maximum token cost per run: ${formatCost(estimate)}`
                  : estimatePrompt}
              </output>
            </p>
          </fieldset>
          {error && <p role="alert">{error}</p>}
          <div className="actions">
            <button type="button" onClick={() => dialog.current.close()}>Cancel</button>
            <button className="primary" type="submit">Save settings</button>
          </div>
        </form>
      </dialog>
    </>
  );
}
