/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        panel: "#0f172a",
        panelMuted: "#1e293b",
      },
    },
  },
  plugins: [],
};
