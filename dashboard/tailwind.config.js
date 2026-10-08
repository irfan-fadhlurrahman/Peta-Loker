/** Peta Loker design tokens (see the design canvas). */
module.exports = {
  content: ["./*.html", "./app.js"],
  theme: {
    extend: {
      colors: {
        paper: "#F5F3EE",
        surface: "#FFFFFF",
        ink: { DEFAULT: "#1B1D1F", soft: "#3D413E", muted: "#5B605C" },
        line: { DEFAULT: "#DEDAD1", soft: "#EEEBE4", strong: "#CFCAC0" },
        teal: { DEFAULT: "#0E6B66", dark: "#0A4F4B", deep: "#073F3C", mid: "#4FA39A", light: "#9CCFC8",
                soft: "#E9F2F0", chip: "#F2F8F7", chipline: "#CFE3DF" },
        amber: { DEFAULT: "#8A4A12", bg: "#FBEBD9", bar: "#C8781E", soft: "#FBF3E8" },
        danger: { DEFAULT: "#8E1F18", bg: "#FBE4E1" },
        ok: { bg: "#E2F0ED" },
      },
      fontFamily: {
        display: ["'Bricolage Grotesque'", "Georgia", "serif"],
        sans: ["'IBM Plex Sans'", "system-ui", "sans-serif"],
        mono: ["'IBM Plex Mono'", "ui-monospace", "Menlo", "monospace"],
      },
      maxWidth: { page: "1360px" },
    },
  },
  plugins: [],
};
