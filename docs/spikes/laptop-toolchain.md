# Spike: the React toolchain on a locked-down laptop

The React starter (plan section 13) needs Node, pnpm and Vite+ on a citizen developer's Windows laptop, with
no admin rights and nothing from the public internet. This is the kit to run first thing at work. It takes
about 30 minutes, puts everything under one folder, and changes nothing outside it. Versions are basis-ui's
pins: Node 26, pnpm 12.18.0, Vite+ 1.5.0.

## Run

Open a normal PowerShell, not as administrator. Fill in the three registry addresses first. Keep the whole
window's output.

```powershell
$root = "$env:LOCALAPPDATA\ctzd-spike"; New-Item -ItemType Directory -Force $root | Out-Null; Set-Location $root
$npm = "https://artifactory.contoso.com/artifactory/api/npm/npm/"            # the registry's npm remote
$pypi = "https://artifactory.contoso.com/artifactory/api/pypi/pypi/simple"   # its PyPI remote
$nodeDist = "https://artifactory.contoso.com/artifactory/nodejs-dist"        # a generic remote of nodejs.org/dist, if any

# 1. Node. Route A: the zip from a generic remote. Route B: the Node wheel through uv, if uv is installed.
$v = "v26.13.0"
Invoke-WebRequest "$nodeDist/$v/node-$v-win-x64.zip" -OutFile node.zip
Expand-Archive node.zip -DestinationPath .; $env:Path = "$root\node-$v-win-x64;$env:Path"
node --version
# If route A fails:  uv tool install --index-url $pypi "nodejs-wheel==26.*"; node --version

# 2. pnpm from the npm remote, installed into a folder of your own.
npm config set registry $npm
npm config set prefix "$root\npm-global"; $env:Path = "$root\npm-global;$env:Path"
npm install -g pnpm@12.18.0
pnpm --version

# 3. A project with the starter's heaviest dependencies; time a cold install, then a warm one.
New-Item -ItemType Directory app, app\src -Force | Out-Null; Set-Location app
Set-Content package.json '{"name":"spike","private":true,"type":"module",
 "dependencies":{"react":"19.3.1","react-dom":"19.3.1","@base-ui/react":"1.9.0","@tanstack/react-query":"5.106.1",
  "@tanstack/react-router":"1.170.46","apache-arrow":"21.2.0","zod":"4.6.7"},
 "devDependencies":{"vite-plus":"1.5.0","typescript":"7.0.2","tailwindcss":"4.3.5","@tailwindcss/vite":"4.3.5",
  "@vitejs/plugin-react":"6.2.1","babel-plugin-react-compiler":"1.0.0","orval":"8.46.0"}}'
(Measure-Command { pnpm install | Out-Host }).TotalSeconds
Remove-Item -Recurse -Force node_modules
(Measure-Command { pnpm install --offline | Out-Host }).TotalSeconds

# 4. pnpm's links without developer mode: expect LinkType Junction or SymbolicLink, and a path printed.
Get-Item node_modules\react | Select-Object LinkType, Target
node -e "console.log(require.resolve('react'))"
# If step 3 failed with EPERM:  pnpm install --config.node-linker=hoisted, and record that it did.

# 5. Vite+ and one page on localhost. Open http://localhost:5180 in the laptop's browser, then Ctrl+C.
Set-Content vite.config.ts 'import react from "@vitejs/plugin-react"; import { defineConfig } from "vite-plus"; export default defineConfig({ plugins: [react()] });'
Set-Content index.html '<!doctype html><div id="root"></div><script type="module" src="/src/main.tsx"></script>'
Set-Content src\main.tsx 'import { createRoot } from "react-dom/client"; createRoot(document.getElementById("root")!).render(<h1>ctzd spike</h1>);'
pnpm exec vp --version
pnpm exec vp build
pnpm exec vp dev --port 5180

# 6. Python 3.14, which basis needs, from the mirror ctzd's install already uses.
uv python install 3.14; uv run --python 3.14 python --version
```

Step 5 stands in for `ctzd dev`, which is not on the internal registry yet: it runs the same page server.
Afterwards, delete `$env:LOCALAPPDATA\ctzd-spike` and the two `npm config` settings (`npm config delete registry`,
`npm config delete prefix`).

## Send back

- For each step: worked or not, and on failure the last 20 lines of output.
- Node's route (A or B) and `node --version`; `pnpm --version`; `vp --version`.
- The two install times in seconds, and the antivirus product if you know it.
- Step 4's `LinkType`, and whether `node_modules=hoisted` was needed.
- Whether the browser showed "ctzd spike" at `http://localhost:5180`.
- Whether Python 3.14 installed.

## What a failure means for the React plan

| Fails | Means | Then |
|---|---|---|
| Node, both routes | No registry route for Node | Ask for a generic remote of nodejs.org/dist, or `install.ps1` serves Node from the platform host as it does uv and MinGit. Nothing else changes. |
| npm remote answers 401 or 404 | Citizens cannot reach npm packages | Ask the registry admins for read access to the npm remote for everyone. Blocks the plan until done. |
| `npm install -g` refused | Global installs are blocked | `ctzd setup` installs pnpm's standalone `@pnpm/exe` into its own folder instead. |
| EPERM on links (step 3 or 4) | No symlinks or junctions without developer mode | ctzd's locked `.npmrc` sets `node-linker=hoisted`: copies instead of links, slower and larger, otherwise the same. |
| Cold install over 5 min, or warm over 1 min | The antivirus scans every file pnpm writes | Ask for an exclusion for the pnpm store and `%LOCALAPPDATA%\ctzd`; ctzd keeps one store for all apps, so only the first install is slow. |
| `vp`, `vp build` or Node itself refused to run | Application control blocks executables and native modules in user folders (Vite+, Rolldown, Oxlint and Tailwind are native) | The serious one: the toolchain cannot run on the laptop. Ask for a signed-path exception; failing that, checks and builds run only in Jenkins and the React plan needs a hosted workspace. |
| The page does not open | The browser or a policy blocks localhost or the port | Try another port. If localhost is blocked, the person cannot see the app before it ships, and `ctzd dev` needs a preview deploy instead. |
| Python 3.14 missing | The mirror lacks 3.14 | basis-api needs 3.14: ask for it on the mirror. Apps cannot use basis until then. |
