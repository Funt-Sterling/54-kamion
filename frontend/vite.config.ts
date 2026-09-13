import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Stripped-down version of the Figma export's config: the Figma Make
// plugins (site.json shell, error-overlay replay, stories kit) only make
// sense inside Figma's preview environment and pulled in `.figma/`, which
// isn't shipped here.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
  },
});
