(function () {
  "use strict";

  var config = window.REPO_HEALTH_ANALYTICS || {};
  var params = new URLSearchParams(window.location.search);
  var excluded = params.get("analytics_test") === "1" || navigator.webdriver === true;
  var validCode = /^[a-z0-9][a-z0-9-]{1,62}$/.test(config.siteCode || "");
  var ready = false;
  var queue = [];

  function send(name, title) {
    if (excluded || !validCode) { return false; }
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

  var script = document.createElement("script");
  script.async = true;
  script.src = "https://gc.zgo.at/count.js";
  script.setAttribute("data-goatcounter",
                      "https://" + config.siteCode + ".goatcounter.com/count");
  script.addEventListener("load", function () {
    ready = true;
    while (queue.length) {
      var event = queue.shift();
      send(event.name, event.title);
    }
  });
  document.head.appendChild(script);
})();
