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
sent = run({search: "?utm_source=reddit&utm_medium=community&utm_campaign=experiment1"});
if (sent.map(x => x.path).join(",") !== "cta-free-scan-reddit-experiment1,cta-stripe-reddit-experiment1") {
  throw new Error("Reddit campaign events were not separated");
}
sent = run({search: "?utm_source=devto&utm_medium=article&utm_campaign=experiment1"});
if (sent.map(x => x.path).join(",") !== "cta-free-scan-devto-experiment1,cta-stripe-devto-experiment1") {
  throw new Error("Dev.to campaign events were not separated");
}
sent = run({search: "?utm_source=youtube&utm_medium=content&utm_campaign=repo-maintenance-basics-1"});
if (sent.map(x => x.path).join(",") !== "cta-free-scan-youtube-repo-maintenance-basics-1,cta-stripe-youtube-repo-maintenance-basics-1") {
  throw new Error("YouTube campaign events were not separated");
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
sent = run({search: "?analytics_test=record"});
if (sent.map(x => x.path).join(",") !== "__analytics_test_free_scan,__analytics_test_stripe,__analytics_test_free_scan,__analytics_test_stripe") {
  throw new Error("verification events were not isolated under test paths");
}
console.log("analytics event tests passed");
