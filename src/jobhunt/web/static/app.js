// Job tracker front end: selection, keyboard shortcuts, toasts and undo (Alpine.js + htmx).
const isTyping = (e) => ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName) || e.target.isContentEditable;
const hasModifier = (e) => e.ctrlKey || e.metaKey || e.altKey;

document.addEventListener("alpine:init", () => {
  Alpine.store("toast", {
    open: false, message: "", undo: null, timer: null, hideJobId: null,
    show(message, undo = null, hideJobId = null) {
      Object.assign(this, { message, undo, hideJobId, open: true });
      clearTimeout(this.timer);
      this.timer = setTimeout(() => { this.open = false; }, hideJobId ? 10000 : 6000);
    },
    // After a hide: record why (the "Company" reason also blocks the company's other jobs).
    reason(key) {
      const id = this.hideJobId;
      this.open = false;
      if (id) fetch(`/jobs/${id}/hide-reason`, { method: "POST", body: new URLSearchParams({ reason: key }) });
    },
    runUndo() { const undo = this.undo; this.open = false; if (undo) undo(); },
  });

  Alpine.store("help", {
    open: false,
    handleKey(e) {
      if (e.key === "Escape" && this.open) { this.open = false; e.preventDefault(); return; }
      if (e.key === "?" && !isTyping(e) && !hasModifier(e)) { this.open = !this.open; e.preventDefault(); }
    },
  });

  // The split view: one list of .job-item rows and the #job-pane detail.
  Alpine.data("jobList", () => ({
    selectedId: null,
    last: null, // {jobId, previous} of the latest status change, for U and the toast
    undoing: null,
    paneTimer: null,

    init() {
      const selected = this.list().querySelector('.job-item[aria-selected="true"]');
      if (selected) { this.selectedId = selected.dataset.jobId; selected.scrollIntoView({ block: "nearest" }); }
      // A pane that arrives after the selection moved on belongs to another job: don't show it.
      document.body.addEventListener("htmx:beforeSwap", (e) => {
        const match = (e.detail.requestConfig?.path || "").match(/^\/jobs\/(\d+)\/pane/);
        if (match && e.detail.target?.id === "job-pane" && match[1] !== this.selectedId) e.detail.shouldSwap = false;
      });
      document.body.addEventListener("htmx:afterSwap", (e) => {
        if (e.detail.target?.id === "job-pane") e.detail.target.removeAttribute("aria-busy");
      });
      document.body.addEventListener("htmx:afterSettle", () => {
        // Rows the server sends back (after a status change or a new step) arrive unselected: restore the selection.
        this.syncSelection();
        // An answer about a job you've since left replaced the pane (its row update still lands): show the selected job.
        const shown = document.querySelector('#job-pane [id^="pane-title-"]');
        const selected = this.row(this.selectedId);
        if (shown && selected && shown.id !== `pane-title-${this.selectedId}`) this.loadPane(selected);
      });
      const failed = (e) => {
        this.undoing = null;
        const pane = document.getElementById("job-pane");
        if (pane?.getAttribute("aria-busy") && /^\/jobs\/\d+\/pane/.test(e.detail.requestConfig?.path || "")) {
          pane.removeAttribute("aria-busy");
          pane.innerHTML = '<p class="pane-empty">Couldn\'t load this job. Select it again to retry.</p>';
        }
      };
      document.body.addEventListener("htmx:responseError", failed);
      document.body.addEventListener("htmx:sendError", failed);
    },
    list() { return this.$root.querySelector("#job-list"); },
    rows() { return [...this.list().querySelectorAll(".job-item:not(.is-removed)")]; },
    row(id) { return document.getElementById(`job-item-${id}`); },
    syncSelection() {
      for (const r of this.list().querySelectorAll(".job-item")) {
        const want = r.dataset.jobId === this.selectedId ? "true" : "false";
        if (r.getAttribute("aria-selected") !== want) r.setAttribute("aria-selected", want);
      }
    },

    select(row, { load = true } = {}) {
      if (!row) return;
      this.list().querySelectorAll('.job-item[aria-selected="true"]').forEach((r) => r.setAttribute("aria-selected", "false"));
      row.setAttribute("aria-selected", "true");
      row.classList.remove("unseen");
      row.focus({ preventScroll: true });
      row.scrollIntoView({ block: "nearest" });
      this.selectedId = row.dataset.jobId;
      if (load) this.loadPane(row);
      const url = new URL(location.href);
      url.searchParams.set("job", row.dataset.jobId);
      history.replaceState(null, "", url);
      this.$root.classList.add("has-selection");
    },
    // Wait a moment so skimming with J/K only loads (and downloads) the job you stop on.
    loadPane(row) {
      const id = row.dataset.jobId;
      clearTimeout(this.paneTimer);
      const pane = document.getElementById("job-pane");
      // Keep the previous job in place, dimmed, until the next one arrives: removing it would cut off the
      // answer to a status change still on its way (htmx finds the row to update from the clicked button).
      if (pane.querySelector('[id^="pane-title-"]')) pane.setAttribute("aria-busy", "true");
      else pane.innerHTML = '<p class="pane-loading">Loading…</p>';
      this.paneTimer = setTimeout(() => {
        const current = this.row(id); // the row may have been swapped in the meantime
        if (id !== this.selectedId || !current) return;
        // Each row is its own request source, so htmx never queues one job's load behind another's.
        htmx.ajax("GET", current.dataset.paneUrl, { source: current, target: "#job-pane", swap: "innerHTML" });
      }, 200);
    },
    move(step) {
      const rows = this.rows();
      if (!rows.length) return;
      const i = rows.findIndex((r) => r.dataset.jobId === this.selectedId);
      this.select(rows[i < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, i + step))]);
    },
    onClick(e) { const row = e.target.closest(".job-item"); if (row) this.select(row); },
    closePane() { this.$root.classList.remove("has-selection"); },
    act(name) {
      // Only act on the job you're looking at: the pane must already show the selected job.
      const title = document.querySelector('#job-pane [id^="pane-title-"]');
      if (!title || title.id !== `pane-title-${this.selectedId}`) return;
      const el = document.querySelector(`#job-pane [data-action="${name}"]`);
      if (el) el.click();
    },

    // Sent by the server (HX-Trigger-After-Settle) after a status change from the pane.
    onStatus({ jobId, status, previous, label }) {
      const id = String(jobId);
      const row = this.row(id);
      if (row && id === this.selectedId) row.setAttribute("aria-selected", "true");
      // The answer replaced the pane after you'd moved on: show the selected job again.
      if (id !== this.selectedId && this.row(this.selectedId)) this.loadPane(this.row(this.selectedId));
      if (this.undoing === id) { this.undoing = null; return; }
      const rows = this.rows();
      const i = rows.indexOf(row);
      if (row && status === "hidden" && this.list().dataset.hideHidden === "true") row.classList.add("is-removed");
      if (status !== previous) {
        this.last = { jobId: id, previous };
        Alpine.store("toast").show(label, () => this.undo(), status === "hidden" ? id : null);
      }
      if (row && id === this.selectedId && status !== "new" && i >= 0) {
        const next = rows[i + 1] || rows[i - 1];
        if (next && next !== row) this.select(next);
      }
    },
    undo() {
      if (!this.last) return;
      const { jobId, previous } = this.last;
      this.last = null;
      this.undoing = jobId;
      const row = this.row(jobId);
      if (row) { row.classList.remove("is-removed"); this.select(row, { load: false }); }
      clearTimeout(this.paneTimer);
      // The answer carries the restored job's pane; its own source keeps later loads from replacing it.
      htmx.ajax("POST", `/jobs/${jobId}/status`, {
        source: row || document.body, values: { status: previous, view: "pane" }, target: "#job-pane", swap: "innerHTML",
      });
      Alpine.store("toast").open = false;
    },

    onKey(e) {
      if (isTyping(e) || hasModifier(e) || Alpine.store("help").open) return;
      const actions = { s: "save", a: "applied", x: "hide", c: "cv", o: "linkedin" };
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      if (e.repeat && !["j", "k", "ArrowDown", "ArrowUp"].includes(key)) return; // a held X mustn't hide the next job too
      if (key === "j" || key === "ArrowDown") this.move(1);
      else if (key === "k" || key === "ArrowUp") this.move(-1);
      else if (actions[key]) this.act(actions[key]);
      else if (key === "u") this.undo();
      else if (key === "f" && this.$root.dataset.focusUrl) location.href = this.$root.dataset.focusUrl;
      else if (key === "/" && document.getElementById("search")) document.getElementById("search").focus();
      else return;
      e.preventDefault();
    },
  }));

  // Theme switch: System follows the OS; Light and Dark are remembered in this browser.
  Alpine.data("themeSwitch", () => ({
    choice: "system",
    init() { this.choice = document.documentElement.dataset.theme || "system"; },
    set(value) {
      this.choice = value;
      try { value === "system" ? localStorage.removeItem("theme") : localStorage.setItem("theme", value); } catch (e) {}
      if (value === "system") delete document.documentElement.dataset.theme;
      else document.documentElement.dataset.theme = value;
    },
  }));

  // Filter bar: leave empty fields and the default sort out of the URL.
  Alpine.data("filterBar", () => ({
    init() {
      // Back/forward can restore the page as you left it; show the filters this URL actually applies.
      window.addEventListener("pageshow", () => this.$root.reset());
    },
    clean(e) {
      for (const [key, value] of [...e.formData.entries()]) {
        if (value === "" || (key === "sort" && value === "found")) e.formData.delete(key);
      }
    },
  }));

  // Focus mode: one job at a time; keys submit the matching form.
  Alpine.data("focusPage", () => ({
    onKey(e) {
      if (isTyping(e) || hasModifier(e) || Alpine.store("help").open || e.repeat) return;
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      const forms = { x: "hide", s: "save", a: "applied", l: "later" };
      if (forms[key]) this.$root.querySelector(`form[data-focus-action="${forms[key]}"]`)?.requestSubmit();
      else if (key === "c" || key === "o") document.querySelector(`[data-action="${key === "c" ? "cv" : "linkedin"}"]`)?.click();
      else if (key === "Escape") document.getElementById("focus-back")?.click();
      else return;
      e.preventDefault();
    },
  }));
});
