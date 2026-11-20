/* Sunrise notification switch on My account (iPhone home-screen app, desktop and Android browsers). */
(function () {
  var box = document.getElementById("push-pref");
  if (!box) return;
  var key = box.dataset.key;
  var toggle = document.getElementById("push-toggle");
  var testBtn = document.getElementById("push-test");
  var msg = document.getElementById("push-msg");
  var csrf = document.querySelector('meta[name="csrf-token"]').content;
  var isIOS = /iPhone|iPad|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var standalone = window.navigator.standalone === true ||
    (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches);

  function show(id) {
    ["push-ready", "push-install", "push-unsupported", "push-blocked"].forEach(function (x) {
      var el = document.getElementById(x);
      if (el) el.hidden = x !== id;
    });
    box.hidden = false;
  }
  function say(text, bad) {
    msg.textContent = text || "";
    msg.className = "small mt-2 " + (bad ? "text-danger" : "text-success");
  }
  function post(url, body) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok || !j.ok) throw new Error(j.error || "Something went wrong (" + r.status + ").");
        return j;
      });
    });
  }
  function keyBytes(b64) {
    var pad = "=".repeat((4 - (b64.length % 4)) % 4);
    var raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  function registration() {
    return navigator.serviceWorker.register("/sw.js", { scope: "/" }).then(function () {
      return navigator.serviceWorker.ready;
    });
  }

  var supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  if (isIOS && !standalone) { show("push-install"); return; }       // iPhone: only the home-screen app gets notifications
  if (!supported || !key) { show("push-unsupported"); return; }
  if (Notification.permission === "denied") { show("push-blocked"); return; }
  show("push-ready");

  registration().then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
    toggle.checked = !!sub;
    testBtn.hidden = !sub;
    if (sub) post("/push/subscribe", sub.toJSON()).catch(function () {}); // keep the server's copy current
  });

  toggle.addEventListener("change", function () {
    toggle.disabled = true;
    say("");
    var work;
    if (toggle.checked) {
      // Must be called straight from the tap, or iPhones won't show the "Allow notifications?" question.
      work = Notification.requestPermission().then(function (perm) {
        if (perm !== "granted") {
          if (perm === "denied") show("push-blocked");
          throw new Error("Notifications weren't allowed.");
        }
        return registration();
      }).then(function (reg) {
        return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
      }).then(function (sub) {
        return post("/push/subscribe", sub.toJSON());
      }).then(function () {
        testBtn.hidden = false;
        say("On. You'll get the Sunrise report here every morning at 6:00.");
      });
    } else {
      work = registration().then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
        if (!sub) return;
        var body = sub.toJSON();
        return sub.unsubscribe().then(function () { return post("/push/unsubscribe", body); });
      }).then(function () {
        testBtn.hidden = true;
        say("Off.");
      });
    }
    work.catch(function (e) {
      toggle.checked = !toggle.checked;
      say(e.message, true);
    }).then(function () { toggle.disabled = false; });
  });

  testBtn.addEventListener("click", function () {
    testBtn.disabled = true;
    registration().then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
      if (!sub) throw new Error("This device isn't signed up. Turn the switch on first.");
      return post("/push/test", sub.toJSON());
    }).then(function () {
      say("Sent. It should arrive in a few seconds.");
    }).catch(function (e) { say(e.message, true); }).then(function () { testBtn.disabled = false; });
  });
})();
