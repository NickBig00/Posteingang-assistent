/**
 * Tailwind build configuration (scripts/build_css.py compiles app/core/static/css/app.css).
 * Design tokens live as CSS custom properties in app/core/static/src/app.css; see docs/DESIGN.md.
 * @type {import('tailwindcss').Config}
 */
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

module.exports = {
  content: ["./app/**/templates/**/*.html"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
      },
      colors: {
        brand: {
          DEFAULT: token("brand"),
          on: token("brand-on"),
          strong: token("brand-strong"),
          text: token("brand-text"),
          soft: token("brand-soft"),
        },
        surface: { DEFAULT: token("surface"), muted: token("surface-muted"), sunken: token("surface-sunken") },
        line: { DEFAULT: token("line"), strong: token("line-strong") },
        ink: { DEFAULT: token("ink"), muted: token("ink-muted"), subtle: token("ink-subtle") },
      },
      borderRadius: { card: "0.75rem" },
      boxShadow: { card: "0 1px 2px 0 rgb(15 23 42 / 0.05), 0 1px 3px 0 rgb(15 23 42 / 0.04)" },
    },
  },
  plugins: [],
};
