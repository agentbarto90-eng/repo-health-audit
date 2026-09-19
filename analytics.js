(function () {
  "use strict";

  var config = window.REPO_HEALTH_ANALYTICS || {};
  var params = new URLSearchParams(window.location.search);
  var testMode = params.get("analytics_test");
  var verification = testMode === "record";
  var excluded = testMode === "1" || navigator.webdriver === true;
  var validCode = /^[a-z0-9][a-z0-9-]{1,62}$/.test(config.siteCode || "");
  var ready = false;
  var queue = [];

  function send(name, title) {
    if (excluded || !validCode) { return false; }
    if (verification && name.indexOf("__analytics_test_") !== 0) {
      name = "__analytics_test_" + name.replace(/^cta-/, "").replace(/-/g, "_");
      title = "Analytics verification only: " + title;
    }
    if (ready && window.goatcounter && typeof window.goatcounter.count === "function") {
      window.goatcounter.count({path: name, title: title, event: true});
      return true;
    }
    queue.push({name: name, title: title});
    return false;
  }

  window.repoHealthTrack = send;

  document.querySelectorAll("[data-analytics-event]").forEach(function (element) {
    element.addEventListener("click", function () {
      send(element.getAttribute("data-analytics-event"),
           element.getAttribute("data-analytics-title") || element.textContent.trim());
    });
  });

  if (excluded || !validCode || config.provider !== "goatcounter") { return; }

  if (verification) {
    var existingCount = window.goatcounter && window.goatcounter.count;
    window.goatcounter = {
      path: "/__analytics_test_page",
      title: "Analytics verification only",
      referrer: "operator test; excluded from experiment metrics"
    };
    if (existingCount) { window.goatcounter.count = existingCount; }
  }

  var script = document.createElement("script");
  script.async = true;
  script.src = "https://gc.zgo.at/count.js";
  script.setAttribute("data-goatcounter",
                      "https://" + config.siteCode + ".goatcounter.com/count");
  script.addEventListener("load", function () {
    ready = true;
    if (verification) {
      send("__analytics_test_free_scan", "Analytics verification: free scan CTA");
      send("__analytics_test_stripe", "Analytics verification: Stripe CTA");
    }
    while (queue.length) {
      var event = queue.shift();
      send(event.name, event.title);
    }
  });
  document.head.appendChild(script);
})();
