// Platform-owned: ctzd sync rewrites this file. The firm's checks, plugins and tasks, from @basis/ui's preset.
import { basisPlugins, basisPreset, basisTest } from "@basis/ui/preset";
import { defineConfig } from "vite-plus";

export default defineConfig({
  ...basisPreset(),
  plugins: basisPlugins(),
  test: await basisTest(),
});
