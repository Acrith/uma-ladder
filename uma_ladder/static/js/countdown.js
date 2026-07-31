/* PR-T1/T2 — shared countdown renderer.
 *
 * Any element with [data-countdown] gets:
 *   - inner [data-local] (optional) rendered as the viewer's locale
 *     date/time, once on load (doesn't change as time passes)
 *   - inner [data-relative] (optional) rendered as a relative tail
 *     like "starts in 5d 14h" or "started 12m ago" — updated on a
 *     cadence driven by [data-update-interval-ms] (default 30000)
 *
 * Markup:
 *   <span data-countdown
 *         data-utc-iso="2026-05-17T23:00:00Z"
 *         data-prefix-future="starts in"
 *         data-prefix-past="started"
 *         data-suffix-past="ago"
 *         data-update-interval-ms="30000">
 *     <span data-local>2026-05-17 23:00 UTC</span>
 *     <span data-relative></span>
 *   </span>
 *
 * Server-side fallback: write a sensible UTC string into [data-local]
 * so the page is readable without JS. Once JS loads we replace it
 * with the locale-formatted version.
 *
 * Used by:
 *   - Official race detail Scheduled block (PR-T1)
 *   - Official race detail room-code expiry countdown (PR-T2)
 *   - Dashboard "Official Races" tile (PR-T2)
 *   - /official/ list (PR-T2)
 *
 * Idempotent — multiple loads + repeated `init()` calls don't
 * double-bind. Safe to include unconditionally; no-op when no
 * [data-countdown] elements exist on the page.
 */
(function () {
  if (window.__umaladderCountdownInited) return;
  window.__umaladderCountdownInited = true;

  var localFmt = new Intl.DateTimeFormat(undefined, {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });

  function fmtDelta(ms, granularity) {
    var abs = Math.abs(ms);
    var totalSec = Math.floor(abs / 1000);
    var days = Math.floor(totalSec / 86400);
    var hours = Math.floor((totalSec % 86400) / 3600);
    var minutes = Math.floor((totalSec % 3600) / 60);
    var seconds = totalSec % 60;
    if (days > 0) return days + "d " + hours + "h";
    if (hours > 0) return hours + "h " + minutes + "m";
    if (minutes > 0) {
      // Second-level precision when we're close enough that it
      // matters (callers signal via update-interval-ms < 5000).
      if (granularity === "seconds") return minutes + "m " + seconds + "s";
      return minutes + "m";
    }
    if (granularity === "seconds") return seconds + "s";
    return "<1m";
  }

  // Group elements by their interval so we run one timer per
  // cadence instead of N timers when there are many countdowns
  // on the page (e.g. /official/ list with 20 races).
  var bucketsByInterval = {};

  function registerEl(el) {
    var iso = el.getAttribute("data-utc-iso");
    if (!iso) return;
    var dt = new Date(iso);
    if (Number.isNaN(dt.getTime())) return;

    var localEl = el.querySelector("[data-local]");
    if (localEl) {
      // Render once — locale time doesn't change as the clock ticks.
      localEl.textContent = localFmt.format(dt);
    }

    var relEl = el.querySelector("[data-relative]");
    if (!relEl) return;

    var interval = parseInt(
      el.getAttribute("data-update-interval-ms") || "30000",
      10
    );
    if (!(interval > 0)) interval = 30000;

    var ctx = {
      dt: dt,
      el: el,
      relEl: relEl,
      futurePrefix: el.getAttribute("data-prefix-future") || "starts in",
      pastPrefix: el.getAttribute("data-prefix-past") || "started",
      pastSuffix: el.getAttribute("data-suffix-past") || "ago",
      granularity: interval < 5000 ? "seconds" : "minutes",
      futureClass:
        el.getAttribute("data-class-future") || "text-amber-300",
      pastClass: el.getAttribute("data-class-past") || "text-slate-400",
      // Size/weight classes applied alongside the tone class. The
      // dashboard next-race panel renders its countdown as the
      // panel's headline number; everything else keeps text-xs.
      sizeClass: el.getAttribute("data-class-size") || "text-xs",
    };

    bucketsByInterval[interval] = bucketsByInterval[interval] || [];
    bucketsByInterval[interval].push(ctx);
  }

  function tickCtx(now, ctx) {
    var diff = ctx.dt.getTime() - now;
    if (diff > 0) {
      ctx.relEl.textContent =
        ctx.futurePrefix + " " + fmtDelta(diff, ctx.granularity);
      ctx.relEl.className = ctx.sizeClass + " " + ctx.futureClass;
    } else {
      ctx.relEl.textContent =
        ctx.pastPrefix + " " + fmtDelta(diff, ctx.granularity) +
        (ctx.pastSuffix ? " " + ctx.pastSuffix : "");
      ctx.relEl.className = ctx.sizeClass + " " + ctx.pastClass;
    }
  }

  function init() {
    var elements = document.querySelectorAll("[data-countdown]");
    if (!elements.length) return;
    elements.forEach(registerEl);
    Object.keys(bucketsByInterval).forEach(function (k) {
      var interval = parseInt(k, 10);
      var ctxs = bucketsByInterval[k];
      function tickAll() {
        var now = Date.now();
        ctxs.forEach(function (ctx) { tickCtx(now, ctx); });
      }
      tickAll();
      setInterval(tickAll, interval);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
