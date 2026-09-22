/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        panel: "#0f172a",
        panelMuted: "#1e293b",
        // --- PROTOTYPE tokens (prototype/frontend-redesign branch only) ---
        base: "#0A0F1A",
        surface: "#111A29",
        raised: "#1A2436",
        line: "#22304A",
        ink: "#E6EDF3",
        "ink-dim": "#94A3B8",
        "ink-mute": "#5B6B7F",
        signal: "#2DD4BF",
        ok: "#34D399",
        warn: "#FBBF24",
        danger: "#F87171",
        circuit: "#FB923C",
        // world D — blueprint
        "bp-base": "#0B1026",
        "bp-panel": "#101736",
        "bp-line": "#26315E",
        "bp-ink": "#DCE6FF",
        "bp-dim": "#7E8DB8",
        "bp-signal": "#63E6FF",
        "bp-ok": "#3DDC97",
        "bp-warn": "#FFB454",
        "bp-danger": "#FF6B6B",
        // world E — atelier
        "ed-base": "#141210",
        "ed-raised": "#1E1A16",
        "ed-line": "#33291F",
        "ed-ink": "#EDE4D3",
        "ed-dim": "#A39A8B",
        "ed-accent": "#E07856",
        "ed-ok": "#8FAE8B",
        "ed-warn": "#D9A441",
        "ed-danger": "#D1604F",
        // world F — phosphor
        "ph-base": "#0B0A07",
        "ph-panel": "#14110B",
        "ph-line": "#2E2514",
        "ph-amber": "#FFB000",
        "ph-bright": "#FFD479",
        "ph-dim": "#8A6D2F",
        "ph-danger": "#FF5C47",
      },
      fontFamily: {
        display: ['"Space Grotesk"', "ui-sans-serif", "system-ui", "sans-serif"],
        serif: ['"Fraunces"', "Georgia", "serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
