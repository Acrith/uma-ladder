/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./uma_ladder/**/*.html", "./uma_ladder/**/*.py"],
  // PR-P1 — these classes are constructed at render time inside
  // Jinja macros (templates/_avatar.html), so the static scanner
  // can't see them as literals. Without this safelist they'd be
  // purged from the prod build and avatar borders would silently
  // render unstyled. Keep this list in sync with the
  // AVATAR_BORDER_PALETTE in uma_ladder/services/profiles.py.
  safelist: [
    "ring-cyan-400",
    "ring-fuchsia-400",
    "ring-emerald-400",
    "ring-amber-400",
    "ring-rose-400",
    "ring-violet-400",
    "ring-sky-400",
    "ring-indigo-400",
    "ring-lime-400",
    "ring-orange-400",
    "ring-pink-400",
    "ring-slate-400",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Inter"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      colors: {
        ink: {
          950: "#070912",
          900: "#0d111c",
          800: "#171c2c",
          700: "#222942",
        },
      },
      boxShadow: {
        "glow-cyan": "0 0 20px rgba(34, 211, 238, 0.25)",
        "glow-cyan-strong": "0 0 30px rgba(34, 211, 238, 0.45)",
        "glow-fuchsia": "0 0 20px rgba(232, 121, 249, 0.3)",
        "glow-amber": "0 0 18px rgba(251, 191, 36, 0.25)",
      },
      keyframes: {
        "live-pulse": {
          "0%, 100%": { opacity: "1", transform: "scale(1)" },
          "50%": { opacity: "0.55", transform: "scale(0.92)" },
        },
      },
      animation: {
        "live-pulse": "live-pulse 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
