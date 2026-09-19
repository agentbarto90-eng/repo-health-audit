const fs = require("fs");
const vm = require("vm");

function run({search = "", webdriver = false, siteCode = "repo-health-audit"} = {}) {
  const sent = [];
  const listeners = {};
  const elements = [
    {
      attrs: {"data-analytics-event": "cta-free-scan", "data-analytics-title": "Free scan CTA"},
      textContent: "Request the free scan",
      addEventListener(type, callback) { listeners["free-" + type] = callback; },
      getAttribute(name) { return this.attrs[name]; },
    },
    {
      attrs: {"data-analytics-event": "cta-stripe", "data-analytics-title": "Stripe £29 CTA"},
      textContent: "Pay £29",
      addEventListener(type, callback) { listeners["stripe-" + type] = callback; },
      getAttribute(name) { return this.attrs[name]; },
    },
  ];
  const scriptListeners = {};
  const context = {
    URLSearchParams,
    navigator: {webdriver},
    window: {
      location: {search},
      REPO_HEALTH_ANALYTICS: {provider: "goatcounter", siteCode},
      goatcounter: {count(event) { sent.push(event); }},
    },
    document: {
      querySelectorAll() { return elements; },
      createElement() {
        return {
          setAttribute() {},
          addEventListener(type, callback) { scriptListeners[type] = callback; },
        };
      },
      head: {appendChild() { scriptListeners.load(); }},
    },
  };
  vm.runInNewContext(fs.readFileSync("analytics.js", "utf8"), context);
  listeners["free-click"]();
  listeners["stripe-click"]();
  return sent;
}

let sent = run();
if (sent.map(x => x.path).join(",") !== "cta-free-scan,cta-stripe") {
  throw new Error("CTA events were not emitted correctly");
}
if (run({search: "?analytics_test=1"}).length !== 0) {
  throw new Error("operator/test exclusion failed");
}
if (run({webdriver: true}).length !== 0) {
  throw new Error("automation exclusion failed");
}
if (run({siteCode: ""}).length !== 0) {
  throw new Error("inactive configuration emitted events");
}
console.log("analytics event tests passed");
