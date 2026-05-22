/**
 * PriceHawk Lovelace card — Phase 10 PR-14.
 *
 * Companion to the v2 panel (PR-13). Reads the chosen-plan cost
 * sensor introduced in Phase 9 PR-11 and renders a compact card the
 * user can drop into any Lovelace dashboard.
 *
 * Resource auto-registered via `homeassistant.components.frontend
 * .async_register_resource` on entry setup — no manual "Add resource"
 * step required.
 */

import {
  LitElement,
  html,
  css,
} from "https://unpkg.com/lit-element@4.2.0/lit-element.js?module";

class PriceHawkCostCard extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      _config: { type: Object, state: true },
    };
  }

  static get styles() {
    return css`
      :host {
        display: block;
      }
      .card {
        background: var(--card-background-color, #fff);
        border-radius: 12px;
        padding: 16px;
        box-shadow: var(
          --ha-card-box-shadow,
          0 1px 2px rgba(0, 0, 0, 0.05)
        );
      }
      .title {
        font-size: 0.85rem;
        color: var(--secondary-text-color, #757575);
        margin-bottom: 4px;
      }
      .cost {
        font-size: 1.8rem;
        font-weight: 600;
        color: var(--primary-color, #ff8c00);
      }
      .savings {
        font-size: 0.9rem;
        color: var(--secondary-text-color, #757575);
        margin-top: 8px;
      }
      .pos {
        color: var(--success-color, #2e7d32);
      }
      .neg {
        color: var(--error-color, #c62828);
      }
    `;
  }

  setConfig(config) {
    if (!config) {
      throw new Error("Invalid configuration");
    }
    this._config = {
      entity: config.entity || "sensor.pricehawk_today_cost",
      savings_entity: config.savings_entity || "sensor.pricehawk_saving_today",
      title: config.title || "PriceHawk today",
    };
  }

  getCardSize() {
    return 2;
  }

  static getStubConfig() {
    return {
      type: "custom:pricehawk-cost-card",
      title: "PriceHawk today",
      entity: "sensor.pricehawk_today_cost",
      savings_entity: "sensor.pricehawk_saving_today",
    };
  }

  render() {
    if (!this.hass || !this._config) {
      return html`<div class="card">Loading…</div>`;
    }
    const cost = this.hass.states[this._config.entity];
    const savings = this.hass.states[this._config.savings_entity];

    const costValue = cost ? cost.state : "—";
    const costUnit = cost?.attributes?.unit_of_measurement ?? "AUD";

    let savingsText = "";
    let savingsClass = "";
    if (savings && savings.state !== "unknown" && savings.state !== "—") {
      const v = parseFloat(savings.state);
      if (!isNaN(v)) {
        savingsClass = v >= 0 ? "pos" : "neg";
        savingsText = `Saving today: $${v.toFixed(2)}`;
      }
    }

    return html`
      <ha-card>
        <div class="card">
          <div class="title">${this._config.title}</div>
          <div class="cost">$${costValue} ${costUnit}</div>
          ${savingsText
            ? html`<div class="savings ${savingsClass}">${savingsText}</div>`
            : ""}
        </div>
      </ha-card>
    `;
  }
}

if (!customElements.get("pricehawk-cost-card")) {
  customElements.define("pricehawk-cost-card", PriceHawkCostCard);
}

// Register with HA Lovelace's custom card catalogue so it appears in
// the "Add Card" picker.
window.customCards = window.customCards || [];
if (!window.customCards.find((c) => c.type === "pricehawk-cost-card")) {
  window.customCards.push({
    type: "pricehawk-cost-card",
    name: "PriceHawk Today Cost",
    description:
      "Today's cost on the chosen plan, with optional savings line.",
    preview: false,
  });
}
