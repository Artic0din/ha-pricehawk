/**
 * PriceHawk v2 panel — Phase 10 PR-13.
 *
 * Lit-based ``panel_custom`` element. Replaces the iframe-+-LLAT
 * approach of the legacy ``www/dashboard.html`` with a proper HA panel
 * that reuses the host page's WebSocket connection (auth-via-cookie,
 * no LLAT in URL).
 *
 * The full visual port lives in a follow-up dedicated to Playwright UAT
 * — this file establishes the registration mechanism + a minimal
 * functional placeholder that shows the chosen-plan cost sensor
 * (from Phase 9 PR-11) so users can see the v2 panel is wired.
 *
 * Imports Lit from the unpkg CDN as ESM modules — no build step
 * required. HACS distribution carries this file verbatim into
 * ``/local/pricehawk/pricehawk-panel.js``.
 */

import {
  LitElement,
  html,
  css,
} from "https://unpkg.com/lit-element@4.2.0/lit-element.js?module";

class PriceHawkPanel extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      narrow: { type: Boolean },
      panel: { type: Object },
    };
  }

  static get styles() {
    return css`
      :host {
        display: block;
        padding: 24px;
        font-family:
          var(--paper-font-body1_-_font-family, "Roboto", sans-serif);
        color: var(--primary-text-color, #212121);
      }
      .card {
        background: var(--card-background-color, #fff);
        border-radius: 12px;
        padding: 20px 24px;
        box-shadow: var(
          --ha-card-box-shadow,
          0 1px 2px rgba(0, 0, 0, 0.05)
        );
        margin-bottom: 16px;
      }
      h1 {
        margin: 0 0 8px;
        font-size: 1.6rem;
        color: var(--primary-color, #ff8c00);
      }
      h2 {
        margin: 0 0 12px;
        font-size: 1.1rem;
      }
      p {
        margin: 4px 0;
        line-height: 1.5;
      }
      .cost {
        font-size: 2.2rem;
        font-weight: 600;
        color: var(--primary-color, #ff8c00);
      }
      .muted {
        color: var(--secondary-text-color, #757575);
        font-size: 0.85rem;
      }
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 16px;
      }
    `;
  }

  _entityState(entity_id) {
    const s = this.hass?.states?.[entity_id];
    return s ? s.state : null;
  }

  _entityAttr(entity_id, attr) {
    return this.hass?.states?.[entity_id]?.attributes?.[attr] ?? null;
  }

  render() {
    if (!this.hass) {
      return html`<div class="card">Loading PriceHawk…</div>`;
    }

    const todayCost = this._entityState("sensor.pricehawk_today_cost");
    const todayCostUnit =
      this._entityAttr("sensor.pricehawk_today_cost", "unit_of_measurement") ??
      "AUD";

    const savingsToday = this._entityState("sensor.pricehawk_saving_today");
    const bestProvider = this._entityState("sensor.pricehawk_best_provider");

    return html`
      <div class="card">
        <h1>PriceHawk v2</h1>
        <p class="muted">
          Real-time cost comparison across your registered retailers.
        </p>
      </div>

      <div class="grid">
        <div class="card">
          <h2>Today's cost (chosen plan)</h2>
          <div class="cost">
            ${todayCost !== null ? `$${todayCost} ${todayCostUnit}` : "—"}
          </div>
          <p class="muted">
            Source: <code>sensor.pricehawk_today_cost</code>. Energy-Dashboard
            pickable.
          </p>
        </div>

        <div class="card">
          <h2>Today's saving</h2>
          <div class="cost">
            ${savingsToday !== null ? `$${savingsToday}` : "—"}
          </div>
          <p class="muted">vs. the cheapest comparator.</p>
        </div>

        <div class="card">
          <h2>Best provider today</h2>
          <div class="cost" style="font-size:1.4rem">
            ${bestProvider ?? "—"}
          </div>
          <p class="muted">
            Lowest projected daily cost across registered providers.
          </p>
        </div>
      </div>

      <div class="card">
        <h2>Coming in v2</h2>
        <p>
          Ranked-alternatives table, per-window TOU breakdown, CSV import
          wizard, blueprint installer. UI port in progress —
          <a href="/local/pricehawk/dashboard.html"
            >open the legacy dashboard</a
          >
          for the full feature surface.
        </p>
        <p class="muted">
          Panel served via HA's <code>panel_custom</code> — auth via your
          HA session, no long-lived access token in URL. (Phase 10 PR-13)
        </p>
      </div>
    `;
  }
}

if (!customElements.get("pricehawk-panel")) {
  customElements.define("pricehawk-panel", PriceHawkPanel);
}
