/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./uma_ladder/**/*.html", "./uma_ladder/**/*.py"],
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
