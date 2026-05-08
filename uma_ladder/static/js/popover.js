// Reusable popover widget (PR-J16).
//
// Why this exists: native <select> + custom <details>-popovers
// both have problems inside `overflow: hidden / auto` containers.
// Native selects get OS popup styling (white-on-white in some
// themes — see PR-J15-followup). Custom <details>-panels rendered
// with `position: absolute` get clipped by the parent's overflow
// box and can't escape (the original PR-J15 bug).
//
// This widget anchors a panel to a trigger via `position: fixed`
// with viewport-computed coords. Fixed positioning escapes any
// `overflow: hidden` ancestor (provided no transform / filter /
// perspective on a parent creates a containing block, which we
// don't have here).
//
// Markup contract:
//
//   <div data-popover>
//     <button type="button" data-popover-trigger>...</button>
//     <div data-popover-panel hidden class="...">...</div>
//   </div>
//
// The trigger MUST be `<button type="button">` so it doesn't
// submit the surrounding `<form>` if there is one. The panel
// starts with `hidden` (Tailwind utility) and gets shown via
// removing that class + computing coordinates.
//
// Behavior:
// - Click trigger → opens panel below the trigger, right-aligned.
//   Flips above the trigger if there's not enough room below.
// - Click another trigger → closes the previous, opens the new.
// - Click anywhere outside an open panel → closes.
// - Escape key → closes.
// - Window resize / scroll → closes (coordinates would go stale
//   anyway and recomputing is more code than it's worth).

(function () {
  "use strict";

  function close(panel) {
    panel.classList.add("hidden");
    panel.style.top = "";
    panel.style.left = "";
  }

  function closeAllExcept(except) {
    document
      .querySelectorAll("[data-popover-panel]:not(.hidden)")
      .forEach(function (p) {
        if (p !== except) close(p);
      });
  }

  function openPanel(trigger, panel) {
    closeAllExcept(panel);
    panel.classList.remove("hidden");
    var triggerRect = trigger.getBoundingClientRect();
    var panelRect = panel.getBoundingClientRect();
    var top = triggerRect.bottom + 4;
    // Right-align the panel to the trigger by default.
    var left = triggerRect.right - panelRect.width;
    // Flip above the trigger if it would overflow the viewport bottom.
    if (top + panelRect.height > window.innerHeight - 8) {
      top = triggerRect.top - panelRect.height - 4;
    }
    // Keep within left edge.
    if (left < 8) left = 8;
    panel.style.top = top + "px";
    panel.style.left = left + "px";
  }

  document.addEventListener("click", function (e) {
    var trigger = e.target.closest("[data-popover-trigger]");
    if (trigger) {
      e.preventDefault();
      var wrapper = trigger.closest("[data-popover]");
      if (!wrapper) return;
      var panel = wrapper.querySelector("[data-popover-panel]");
      if (!panel) return;
      if (panel.classList.contains("hidden")) {
        openPanel(trigger, panel);
      } else {
        close(panel);
      }
      return;
    }
    // Click outside any open panel → close all. Don't close when
    // the click landed inside a panel itself (form submits there).
    if (!e.target.closest("[data-popover-panel]")) {
      closeAllExcept(null);
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeAllExcept(null);
  });

  window.addEventListener("resize", function () {
    closeAllExcept(null);
  });
  window.addEventListener(
    "scroll",
    function () {
      closeAllExcept(null);
    },
    true,
  );
})();
