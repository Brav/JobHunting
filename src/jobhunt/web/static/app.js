// Job tracker front end: selection, keyboard shortcuts, toasts and undo (Alpine.js + htmx).
const isTyping = (e) => ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName) || e.target.isContentEditable;
const hasModifier = (e) => e.ctrlKey || e.metaKey || e.altKey;

document.addEventListener("alpine:init", () => {
  Alpine.store("toast", {
    open: false, message: "", undo: null, timer: null,
    show(message, undo = null) {
      Object.assign(this, { message, undo, open: true });
      clearTimeout(this.timer);
      this.timer = setTimeout(() => { this.open = false; }, 6000);
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

    init() {
      const selected = this.list().querySelector('.job-item[aria-selected="true"]');
      if (selected) { this.selectedId = selected.dataset.jobId; selected.scrollIntoView({ block: "nearest" }); }
    },
    list() { return this.$el.querySelector("#job-list"); },
    rows() { return [...this.list().querySelectorAll(".job-item:not(.is-removed)")]; },
    row(id) { return document.getElementById(`job-item-${id}`); },

    select(row) {
      if (!row) return;
      this.list().querySelectorAll('.job-item[aria-selected="true"]').forEach((r) => r.setAttribute("aria-selected", "false"));
      row.setAttribute("aria-selected", "true");
      row.classList.remove("unseen");
      row.focus({ preventScroll: true });
      row.scrollIntoView({ block: "nearest" });
      this.selectedId = row.dataset.jobId;
      document.getElementById("job-pane").innerHTML = '<p class="pane-loading">Loading…</p>';
      htmx.ajax("GET", row.dataset.paneUrl, { target: "#job-pane", swap: "innerHTML" });
      const url = new URL(location.href);
      url.searchParams.set("job", row.dataset.jobId);
      history.replaceState(null, "", url);
      this.$el.classList.add("has-selection");
    },
    move(step) {
      const rows = this.rows();
      if (!rows.length) return;
      const i = rows.findIndex((r) => r.dataset.jobId === this.selectedId);
      this.select(rows[i < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, i + step))]);
    },
    onClick(e) { const row = e.target.closest(".job-item"); if (row) this.select(row); },
    closePane() { this.$el.classList.remove("has-selection"); },
    act(name) { const el = document.querySelector(`#job-pane [data-action="${name}"]`); if (el) el.click(); },

    // Sent by the server (HX-Trigger-After-Settle) after a status change from the pane.
    onStatus({ jobId, status, previous, label }) {
      const id = String(jobId);
      const row = this.row(id);
      if (row && id === this.selectedId) row.setAttribute("aria-selected", "true");
      if (this.undoing === id) { this.undoing = null; return; }
      const rows = this.rows();
      const i = rows.indexOf(row);
      if (row && status === "hidden" && this.list().dataset.hideHidden === "true") row.classList.add("is-removed");
      if (status !== previous) {
        this.last = { jobId: id, previous };
        Alpine.store("toast").show(label, () => this.undo());
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
      if (row) { row.classList.remove("is-removed"); this.select(row); }
      htmx.ajax("POST", `/jobs/${jobId}/status`, { values: { status: previous, view: "pane" }, target: "#job-pane", swap: "innerHTML" });
      Alpine.store("toast").open = false;
    },

    onKey(e) {
      if (isTyping(e) || hasModifier(e) || Alpine.store("help").open) return;
      const actions = { s: "save", a: "applied", x: "hide", c: "cv", o: "linkedin" };
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      if (key === "j" || key === "ArrowDown") this.move(1);
      else if (key === "k" || key === "ArrowUp") this.move(-1);
      else if (actions[key]) this.act(actions[key]);
      else if (key === "u") this.undo();
      else if (key === "f" && this.$el.dataset.focusUrl) location.href = this.$el.dataset.focusUrl;
      else if (key === "/" && document.getElementById("search")) document.getElementById("search").focus();
      else return;
      e.preventDefault();
    },
  }));

  // Focus mode: one job at a time; keys submit the matching form.
  Alpine.data("focusPage", () => ({
    onKey(e) {
      if (isTyping(e) || hasModifier(e) || Alpine.store("help").open) return;
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      const forms = { x: "hide", s: "save", a: "applied", l: "later" };
      if (forms[key]) this.$el.querySelector(`form[data-focus-action="${forms[key]}"]`)?.requestSubmit();
      else if (key === "c" || key === "o") document.querySelector(`[data-action="${key === "c" ? "cv" : "linkedin"}"]`)?.click();
      else if (key === "Escape") document.getElementById("focus-back")?.click();
      else return;
      e.preventDefault();
    },
  }));
});
