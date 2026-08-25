(function () {
  "use strict";

  var match = location.pathname.match(/\/i\/([a-z0-9-]+)/);
  if (!match) return;
  var token = match[1];

  var KEY_RE = /^[a-z2-7]{16,64}$/;
  var storageKey = "partyplanner-me";
  var me = "";
  // A URL-supplied key is unvetted until a state response proves the server
  // knows it (admin, or a key this occasion minted — occasion-wide, so a
  // bookmark works even on a link whose scope shows none of its rows);
  // until then it's never sent with writes.
  var meVetted = true;
  var urlMe = new URLSearchParams(location.search).get("me") || "";
  if (KEY_RE.test(urlMe)) {
    me = urlMe;
    meVetted = false;
  } else {
    if (urlMe) scrubUrlKey();
    try {
      var stored = localStorage.getItem(storageKey) || "";
      if (KEY_RE.test(stored)) me = stored;
    } catch (err) { /* storage unavailable: ?me= still works */ }
  }
  var isAdmin = false;

  // A malformed or unrecognized ?me= grants nothing, so remove it from the
  // address bar rather than leaving a dead parameter to be bookmarked or
  // re-shared as if it were a working edit link.
  function scrubUrlKey() {
    var params = new URLSearchParams(location.search);
    if (!params.has("me")) return;
    params.delete("me");
    var query = params.toString();
    try {
      history.replaceState(null, "", location.pathname + (query ? "?" + query : "") + location.hash);
    } catch (err) { /* ignore */ }
  }

  // `scrub` removes ?me= from the address bar too; only pass it once the
  // server has confirmed the key is unknown — a transient load failure must
  // leave the URL intact so a reload can recover the identity.
  function dropUrlKey(scrub) {
    urlMe = "";
    me = "";
    meVetted = true;
    if (scrub) scrubUrlKey();
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

  // Adopts a key for this visit; persists it unless it was borrowed from the
  // URL while this browser already saved a different key of its own —
  // opening someone else's edit link never replaces the visitor's identity.
  // Returns whether this browser durably holds an identity afterwards.
  function saveKey(key) {
    me = key;
    var saved = "";
    try { saved = localStorage.getItem(storageKey) || ""; } catch (err) { /* ignore */ }
    if (urlMe && key === urlMe && KEY_RE.test(saved) && saved !== key) return true;
    try {
      localStorage.setItem(storageKey, key);
      return localStorage.getItem(storageKey) === key;
    } catch (err) {
      return false;
    }
  }

  function editUrl() {
    return location.origin + location.pathname + "?me=" + me;
  }

  // Leaving host mode is a privilege downgrade, so nothing carries across it:
  // the link goes to the bare path — no query, no fragment — and admin keys
  // are never saved, so it renders the ordinary guest view.
  function renderAdminBanner() {
    var banner = document.querySelector(".admin-banner");
    if (!isAdmin) {
      if (banner) banner.remove();
      return;
    }
    if (banner) return;
    banner = el("div", "admin-banner", "Host mode — you can edit or remove anyone's RSVP. ");
    var leave = el("a", null, "Leave host mode");
    leave.href = location.pathname;
    banner.appendChild(leave);
    document.body.insertBefore(banner, document.body.firstChild);
  }

  // A visitor whose identity this browser holds always sees their private
  // edit link, so the post-RSVP bookmark offer isn't the only chance to
  // copy it.
  function renderMeLink(known) {
    var note = document.querySelector(".me-link");
    var saved = "";
    try { saved = localStorage.getItem(storageKey) || ""; } catch (err) { /* ignore */ }
    if (isAdmin || !known || !me || !meVetted || me !== saved) {
      if (note) note.remove();
      return;
    }
    if (!note) {
      note = el("div", "me-link");
      note.appendChild(document.createTextNode("Your "));
      note.appendChild(el("a", null, "private edit link"));
      note.appendChild(document.createTextNode(" — bookmark it to change your RSVPs from any device; don't share it."));
      document.body.insertBefore(note, document.body.firstChild);
    }
    note.querySelector("a").href = editUrl();
  }

  // Writes run one at a time, so a second keyless submit reuses the key
  // minted by the first instead of minting a second identity.
  var writeQueue = Promise.resolve();
  function enqueueWrite(fn) {
    var run = writeQueue.then(fn, fn);
    writeQueue = run.catch(function () {});
    return run;
  }

  // Reads a JSON body if there is one; non-JSON bodies (edge error pages,
  // empty responses) become null rather than a parse error.
  function jsonBody(res) {
    return res.text().then(function (text) {
      try { return JSON.parse(text); } catch (err) { return null; }
    });
  }

  function removeRsvp(eventId, name, status) {
    if (!window.confirm("Remove " + name + "'s RSVP?")) return;
    var payload = { token: token, event_id: eventId, name: name, remove: true };
    enqueueWrite(function () {
      if (me && meVetted) payload.me = me;
      return fetch("/api/rsvp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    })
      .then(function (res) {
        return jsonBody(res).then(function (data) {
          if (!res.ok) {
            throw new Error(data && data.error ? data.error : "remove failed");
          }
          if (!data) throw new Error("remove failed");
          if (status) status.textContent = "RSVP removed.";
          return refresh();
        });
      })
      .catch(function (err) {
        if (status) {
          status.textContent = err && err.message && err.message !== "remove failed"
            ? err.message
            : "Couldn't remove that RSVP — try again?";
        }
      });
  }

  // The name list stays collapsed behind its counts summary until asked
  // for — a popular event is a toggle, not a wall of names. Expansion is
  // remembered per event so the auto-refresh after an RSVP doesn't fold a
  // list the visitor opened.
  var listExpanded = {};

  function renderEvent(eventId, rsvps, more) {
    var list = document.querySelector('[data-rsvps="' + eventId + '"]');
    var counts = document.querySelector('[data-counts="' + eventId + '"]');
    var status = document.querySelector('[data-status="' + eventId + '"]');
    if (!list) return;
    list.textContent = "";
    var collapsed = !listExpanded[eventId];
    var yes = 0, guests = 0, maybe = 0;
    rsvps.forEach(function (r) {
      if (r.response === "yes") { yes += 1; guests += r.party_size; }
      if (r.response === "maybe") maybe += 1 + r.party_size;
      if (collapsed) return;
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
    if (rsvps.length === 0) {
      list.appendChild(el("li", "muted", "No RSVPs yet — be the first!"));
    } else {
      if (!collapsed && more > 0) list.appendChild(el("li", "muted", "+ " + more + " more not shown"));
      var item = el("li");
      var label = collapsed
        ? "Show " + (rsvps.length + more) + " RSVP" + (rsvps.length + more === 1 ? "" : "s")
        : "Hide RSVPs";
      var toggle = el("button", "toggle", label);
      toggle.type = "button";
      toggle.addEventListener("click", function () {
        listExpanded[eventId] = collapsed;
        renderEvent(eventId, rsvps, more);
      });
      item.appendChild(toggle);
      list.appendChild(item);
    }
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
    var owned = rsvps.filter(function (r) { return r.mine; });
    // One key can own several rows on an event, so the form edits the owned
    // row matching the typed name — or the first one when the field is empty.
    // A value guessed from another event counts as empty: an owned row on
    // this event knows better. An untouched host-prefilled name is left
    // alone here — only refresh()'s seeding may replace it, with the name
    // the visitor actually submitted elsewhere.
    var seeded = form.dataset.guessed && form.dataset.seeded &&
      form.elements.name.value === form.dataset.seeded;
    var typed = seeded ? "" : form.elements.name.value.trim().replace(/\s+/g, " ").toLowerCase();
    var mine = null;
    owned.forEach(function (r) {
      if (!mine && r.name.toLowerCase() === typed) mine = r;
    });
    if (!mine && !typed) mine = owned[0] || null;
    if (!mine) {
      // Undo an earlier prefill (e.g. after removing the RSVP), but never
      // clobber a name the visitor typed themselves.
      if (form.dataset.prefilled && form.elements.name.value === form.dataset.prefilled) {
        form.elements.name.value = "";
        form.elements.response.value = "yes";
        form.elements.party_size.value = "0";
        form.querySelector('button[type="submit"]').textContent = "RSVP";
        delete form.dataset.prefilled;
      }
      return;
    }
    if (!form.elements.name.value || seeded) {
      form.elements.name.value = mine.name;
      delete form.dataset.seeded;
      delete form.dataset.guessed;
    }
    // Only fill response/party_size the first time this row appears —
    // a later refresh must not revert choices typed but not yet submitted.
    if (form.dataset.prefilled !== mine.name) {
      form.elements.response.value = mine.response;
      form.elements.party_size.value = mine.party_size;
    }
    form.dataset.prefilled = mine.name;
    form.querySelector('button[type="submit"]').textContent = "Update RSVP";
  }

  // The next event down the page the visitor hasn't RSVP'd to yet
  // (no owned row after the latest refresh), if any.
  function nextUnansweredEvent(afterEventId) {
    var forms = document.querySelectorAll("[data-form]");
    var seen = false;
    for (var i = 0; i < forms.length; i++) {
      var id = forms[i].getAttribute("data-form");
      if (id === afterEventId) { seen = true; continue; }
      if (seen && !forms[i].dataset.prefilled) return id;
    }
    return null;
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
        if (me && me === urlMe && !isAdmin && !state.known) {
          dropUrlKey(true);
          return refresh();
        }
        meVetted = true;
        if (me && me === urlMe && !isAdmin) saveKey(me);
        renderAdminBanner();
        renderMeLink(!!state.known);
        Object.keys(state.events).forEach(function (eventId) {
          renderEvent(eventId, state.events[eventId], (state.more || {})[eventId] || 0);
          prefillMine(eventId, state.events[eventId]);
        });
        // One identity spans the whole occasion, so a name known from the
        // visitor's owned rows seeds the empty forms of events not yet
        // answered — but only when it's unambiguous: a key owning rows under
        // several names (self + guests) gives no safe guess.
        var myName = "";
        var ambiguous = false;
        if (!isAdmin) {
          Object.keys(state.events).forEach(function (eventId) {
            state.events[eventId].forEach(function (r) {
              if (!r.mine) return;
              if (!myName) myName = r.name;
              else if (r.name.toLowerCase() !== myName.toLowerCase()) ambiguous = true;
            });
          });
        }
        var myGuess = (!ambiguous && myName) || "";
        var seed = myGuess || state.prefill;
        if (seed) {
          document.querySelectorAll("[data-form]").forEach(function (form) {
            var input = form.elements.name;
            // A field still holding an untouched earlier seed (guess or
            // host prefill) is fair game: the visitor's own submitted name
            // is authoritative, and a rename flows through to the others.
            if (!input.value || (form.dataset.seeded && input.value === form.dataset.seeded)) {
              input.value = seed;
              form.dataset.seeded = seed;
              if (myGuess) form.dataset.guessed = "1";
              else delete form.dataset.guessed;
            }
          });
        }
      })
      .catch(function () {
        if (!meVetted) dropUrlKey(false);
        document.querySelectorAll(".rsvp-list").forEach(function (list) {
          list.textContent = "";
          list.appendChild(el("li", "muted", "Couldn't load RSVPs — try refreshing."));
        });
      });
  }

  document.querySelectorAll("[data-form]").forEach(function (form) {
    // Any keystroke makes the name the visitor's own; the seeded marker only
    // ever applies to a value the visitor never touched.
    form.elements.name.addEventListener("input", function () {
      delete form.dataset.seeded;
      delete form.dataset.guessed;
    });
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
        // Whitespace-normalized like the server, so rename detection can't
        // mistake a stray space for a different name.
        name: form.elements.name.value.trim().replace(/\s+/g, " "),
        response: form.elements.response.value,
        party_size: parseInt(form.elements.party_size.value || "0", 10),
      };
      // "Update RSVP" under a changed name is a rename: the old row (same
      // owner key) is removed once the new one is written. Confirmed first,
      // since a shared device may instead mean "add someone else".
      var renameFrom = form.dataset.prefilled || "";
      if (renameFrom && renameFrom.toLowerCase() !== payload.name.toLowerCase()) {
        if (!window.confirm(
          'Rename your RSVP from "' + renameFrom + '" to "' + payload.name +
          '"?\n\nChoose Cancel to RSVP "' + payload.name + '" separately instead.'
        )) {
          renameFrom = "";
        }
      }
      enqueueWrite(function () {
        if (me && meVetted) payload.me = me;
        return fetch("/api/rsvp", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }).then(function (res) {
          return jsonBody(res).then(function (data) {
            if (!res.ok) {
              throw new Error(data && data.error ? data.error : "rsvp failed");
            }
            if (!data) throw new Error("rsvp failed");
            var minted = data.me && data.me !== me;
            var held = true;
            if (data.me && !isAdmin) held = saveKey(data.me);
            if (
              data.me &&
              renameFrom &&
              renameFrom.toLowerCase() !== payload.name.toLowerCase()
            ) {
              return fetch("/api/rsvp", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  token: token,
                  event_id: eventId,
                  name: renameFrom,
                  remove: true,
                  me: data.me,
                }),
              }).then(
                function (rmRes) { return { minted: minted, held: held, oldLeft: !rmRes.ok }; },
                function () { return { minted: minted, held: held, oldLeft: true }; }
              );
            }
            return { minted: minted, held: held, oldLeft: false };
          });
        });
      })
        .then(function (result) {
          if (status) {
            status.textContent = form.elements.response.value === "yes"
              ? "Got it — see you there!"
              : "Got it — RSVP saved.";
            if (result.oldLeft) {
              status.textContent += ' But "' + renameFrom +
                '" couldn\'t be removed — it\'s still listed; use its remove button.';
            }
            if (result.minted && !isAdmin) {
              var a = el("a", null, "bookmark your private edit link");
              a.href = editUrl();
              if (result.held) {
                status.textContent += " To change it later, ";
                status.appendChild(a);
                status.appendChild(document.createTextNode(" — it works for every event here, so don't share it."));
              } else {
                // Storage is blocked, so the address bar has to carry the
                // identity: put the key in the URL and insist on the bookmark.
                try { history.replaceState(null, "", editUrl()); urlMe = me; } catch (err) { /* ignore */ }
                status.textContent += " This browser can't remember your RSVP, so ";
                status.appendChild(a);
                status.appendChild(document.createTextNode(" now — it's your only way to change it later; don't share it."));
              }
            }
          }
          return refresh().then(function () {
            if (!status || isAdmin) return;
            var nextId = nextUnansweredEvent(eventId);
            if (!nextId) return;
            status.appendChild(document.createTextNode(" "));
            var next = el("a", "next-event", "Next event ↓");
            next.href = "#" + nextId;
            next.addEventListener("click", function (clickEvt) {
              clickEvt.preventDefault();
              var target = document.getElementById(nextId);
              if (target) target.scrollIntoView({ behavior: "smooth" });
            });
            status.appendChild(next);
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

  // The initial load seeds the write queue, so a submit racing the first
  // state response can't go out before the URL key is vetted (or dropped).
  writeQueue = refresh();
})();
