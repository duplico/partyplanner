(function () {
  "use strict";

  var match = location.pathname.match(/\/i\/([a-z2-7]+)/);
  if (!match) return;
  var token = match[1];

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderEvent(eventId, rsvps) {
    var list = document.querySelector('[data-rsvps="' + eventId + '"]');
    var counts = document.querySelector('[data-counts="' + eventId + '"]');
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

  function refresh() {
    return fetch("/api/state?t=" + token)
      .then(function (res) {
        if (!res.ok) throw new Error("state fetch failed: " + res.status);
        return res.json();
      })
      .then(function (state) {
        Object.keys(state.events).forEach(function (eventId) {
          renderEvent(eventId, state.events[eventId]);
        });
        if (state.prefill) {
          document.querySelectorAll('.rsvp-form input[name="name"]').forEach(function (input) {
            if (!input.value) input.value = state.prefill;
          });
        }
      })
      .catch(function () {
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
      var button = form.querySelector("button");
      button.disabled = true;
      if (status) status.textContent = "Sending…";
      fetch("/api/rsvp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          token: token,
          event_id: eventId,
          name: form.elements.name.value.trim(),
          response: form.elements.response.value,
          party_size: parseInt(form.elements.party_size.value || "0", 10),
        }),
      })
        .then(function (res) {
          if (!res.ok) throw new Error("rsvp failed: " + res.status);
          if (status) {
            status.textContent = form.elements.response.value === "yes"
              ? "Got it — see you there!"
              : "Got it — RSVP saved.";
          }
          return refresh();
        })
        .catch(function () {
          if (status) status.textContent = "Something went wrong — try again?";
        })
        .then(function () {
          button.disabled = false;
        });
    });
  });

  refresh();
})();
