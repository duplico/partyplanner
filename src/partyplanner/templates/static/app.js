(function () {
  "use strict";

  var match = location.pathname.match(/\/i\/([a-z2-7]+)/);
  if (!match) return;
  var token = match[1];

  var KEY_RE = /^[a-z2-7]{16,64}$/;
  var storageKey = "partyplanner-me";
  var me = "";
  // A URL-supplied key is unvetted until a state response proves it's the
  // visitor's (admin, or owns a row); until then it's never sent with writes.
  var meVetted = true;
  var urlMe = new URLSearchParams(location.search).get("me") || "";
  if (KEY_RE.test(urlMe)) {
    me = urlMe;
    meVetted = false;
  } else {
    try {
      var stored = localStorage.getItem(storageKey) || "";
      if (KEY_RE.test(stored)) me = stored;
    } catch (err) { /* storage unavailable: ?me= still works */ }
  }
  var isAdmin = false;

  function dropUrlKey() {
    urlMe = "";
    me = "";
    meVetted = true;
    try {
      var fallback = localStorage.getItem(storageKey) || "";
      if (KEY_RE.test(fallback)) me = fallback;
    } catch (err) { /* ignore */ }
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function saveKey(key) {
    me = key;
    try { localStorage.setItem(storageKey, key); } catch (err) { /* ignore */ }
  }

  function editUrl() {
    return location.origin + location.pathname + "?me=" + me;
  }

  function removeRsvp(eventId, name, status) {
    if (!window.confirm("Remove " + name + "'s RSVP?")) return;
    fetch("/api/rsvp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: token, event_id: eventId, name: name, me: me, remove: true }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("remove failed: " + res.status);
        if (status) status.textContent = "RSVP removed.";
        return refresh();
      })
      .catch(function () {
        if (status) status.textContent = "Couldn't remove that RSVP — try again?";
      });
  }

  function renderEvent(eventId, rsvps) {
    var list = document.querySelector('[data-rsvps="' + eventId + '"]');
    var counts = document.querySelector('[data-counts="' + eventId + '"]');
    var status = document.querySelector('[data-status="' + eventId + '"]');
    if (!list) return;
    list.textContent = "";
    var yes = 0, guests = 0, maybe = 0;
    rsvps.forEach(function (r) {
      if (r.response === "yes") { yes += 1; guests += r.party_size; }
      if (r.response === "maybe") maybe += 1 + r.party_size;
      var item = el("li");
      var label = r.name + (r.party_size > 0 ? " +" + r.party_size : "");
      item.appendChild(el("span", null, label + " "));
      item.appendChild(el("span", "resp", "— " + r.response));
      if (r.mine) item.appendChild(el("span", "you", " (you)"));
      if (r.mine || isAdmin) {
        var del = el("button", "remove", "remove");
        del.type = "button";
        del.title = "Remove this RSVP";
        del.addEventListener("click", function () {
          removeRsvp(eventId, r.name, status);
        });
        item.appendChild(del);
      }
      list.appendChild(item);
    });
    if (rsvps.length === 0) list.appendChild(el("li", "muted", "No RSVPs yet — be the first!"));
    if (counts) {
      var parts = [];
      parts.push(yes + " yes" + (guests > 0 ? " (+" + guests + " guests)" : ""));
      if (maybe > 0) parts.push(maybe + " maybe");
      counts.textContent = "· " + parts.join(" · ");
    }
  }

  function prefillMine(eventId, rsvps) {
    var form = document.querySelector('[data-form="' + eventId + '"]');
    if (!form) return;
    var mine = rsvps.filter(function (r) { return r.mine; })[0];
    if (!mine) return;
    if (!form.elements.name.value) form.elements.name.value = mine.name;
    if (form.elements.name.value === mine.name) {
      form.elements.response.value = mine.response;
      form.elements.party_size.value = mine.party_size;
      form.querySelector('button[type="submit"]').textContent = "Update RSVP";
    }
  }

  function refresh() {
    var url = "/api/state?t=" + token + (me ? "&me=" + me : "");
    return fetch(url)
      .then(function (res) {
        if (!res.ok) throw new Error("state fetch failed: " + res.status);
        return res.json();
      })
      .then(function (state) {
        isAdmin = !!state.admin;
        document.body.classList.toggle("admin", isAdmin);
        var ownsARow = Object.keys(state.events).some(function (eventId) {
          return state.events[eventId].some(function (r) { return r.mine; });
        });
        if (me && me === urlMe && !isAdmin && !ownsARow) {
          dropUrlKey();
          return refresh();
        }
        meVetted = true;
        if (me && me === urlMe && !isAdmin) saveKey(me);
        Object.keys(state.events).forEach(function (eventId) {
          renderEvent(eventId, state.events[eventId]);
          prefillMine(eventId, state.events[eventId]);
        });
        if (state.prefill) {
          document.querySelectorAll('.rsvp-form input[name="name"]').forEach(function (input) {
            if (!input.value) input.value = state.prefill;
          });
        }
      })
      .catch(function () {
        if (!meVetted) dropUrlKey();
        document.querySelectorAll(".rsvp-list").forEach(function (list) {
          list.textContent = "";
          list.appendChild(el("li", "muted", "Couldn't load RSVPs — try refreshing."));
        });
      });
  }

  document.querySelectorAll("[data-form]").forEach(function (form) {
    form.addEventListener("submit", function (evt) {
      evt.preventDefault();
      var eventId = form.getAttribute("data-form");
      var status = document.querySelector('[data-status="' + eventId + '"]');
      var button = form.querySelector('button[type="submit"]');
      button.disabled = true;
      if (status) status.textContent = "Sending…";
      var payload = {
        token: token,
        event_id: eventId,
        name: form.elements.name.value.trim(),
        response: form.elements.response.value,
        party_size: parseInt(form.elements.party_size.value || "0", 10),
      };
      if (me && meVetted) payload.me = me;
      fetch("/api/rsvp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
        .then(function (res) {
          return res.json().then(function (data) {
            if (!res.ok) {
              throw new Error(data && data.error ? data.error : "rsvp failed");
            }
            var minted = data.me && data.me !== me;
            if (data.me && !isAdmin) saveKey(data.me);
            if (status) {
              status.textContent = form.elements.response.value === "yes"
                ? "Got it — see you there!"
                : "Got it — RSVP saved.";
              if (minted && !isAdmin) {
                status.textContent += " To change it later, ";
                var a = el("a", null, "bookmark your private edit link");
                a.href = editUrl();
                status.appendChild(a);
                status.appendChild(document.createTextNode(" — it works for every event here, so don't share it."));
              }
            }
            return refresh();
          });
        })
        .catch(function (err) {
          if (status) {
            status.textContent = err && err.message && err.message !== "rsvp failed"
              ? err.message
              : "Something went wrong — try again?";
          }
        })
        .then(function () {
          button.disabled = false;
        });
    });
  });

  refresh();
})();
