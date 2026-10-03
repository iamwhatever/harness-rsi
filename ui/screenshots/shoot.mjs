// Render ui/screenshots/*.png from the FAKE fixtures with the host's real UI components
// and theme; nothing talks to a gateway. Needs a KiroCrew website checkout:
//   KIROCREW_WEBSITE=/path/to/KiroCrew/website node ui/screenshots/shoot.mjs
// RSI_PROMPT_CHANGE=card.json swaps in one saved prompt-change card; its shot goes to card.json.png, out of git.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'

const here = path.dirname(fileURLToPath(import.meta.url))
const repo = path.resolve(here, '../..')
const site = process.env.KIROCREW_WEBSITE
if (!site) throw new Error('set KIROCREW_WEBSITE to a KiroCrew/website checkout')
const req = createRequire(path.join(site, 'package.json'))
const load = async (m) => import(pathToFileURL(req.resolve(m)).href)
const { createServer } = await load('vite')
const react = (await load('@vitejs/plugin-react')).default
const tailwindcss = (await load('@tailwindcss/vite')).default
const pw = await load('playwright-core')
const nm = path.join(site, 'node_modules')

// The real ui/index.mjs page body, fed the fixtures plus one baseline shot as a "before" image.
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'rsi-shoot-'))
const before = `/@fs${path.join(repo, 'baseline/screenshots/chat.png')}`
// The page body never calls the backend hook; a stub keeps the host SDK barrel out.
fs.writeFileSync(path.join(tmp, 'app-sdk.js'), 'export const useAppApi = () => null\n')
fs.writeFileSync(path.join(tmp, 'harness.css'), '@import "@host/index.css";\n')
fs.writeFileSync(path.join(tmp, 'index.html'),
  '<!doctype html><html lang="en"><body><div id="root"></div><script type="module" src="./main.jsx"></script></body></html>')
fs.writeFileSync(path.join(tmp, 'main.jsx'), `import { createRoot } from 'react-dom/client'
import { initI18n } from '@host/i18n/all'
import { HarnessRsi } from '${path.join(repo, 'ui/index.mjs')}'
import { loadFixtures, outcomes, promptChange } from '${path.join(repo, 'ui/fake-data.mjs')}'
import './harness.css'
initI18n('en')
document.documentElement.setAttribute('data-theme', 'dark')
const real = ${process.env.RSI_REAL ? fs.readFileSync(process.env.RSI_REAL, 'utf8') : 'null'}
const load = async () => ({ ...(await loadFixtures()), images: { prop_bg_tasks: { before: '${before}' } }, ...(real ? { proposals: real.proposals, images: {} } : {}) })
const onOutcomes = async () => ({ outcomes: real ? real.outcomes : outcomes, score: null })
const onPromptChanges = async () => [${process.env.RSI_PROMPT_CHANGE ? fs.readFileSync(process.env.RSI_PROMPT_CHANGE, 'utf8') : 'promptChange'}]
const onRefresh = async () => ({ total: 10, added: 0, errors: [] })
const onStatus = async () => ({ running: false, started_at: null, finished_at: null, rows: null, error: '' })
createRoot(document.getElementById('root')).render(<div className="flex flex-col h-screen bg-bg text-text"><HarnessRsi load={load} onRefresh={onRefresh} onStatus={onStatus} onOutcomes={onOutcomes} onPromptChanges={onPromptChanges} /></div>)
`)
const alias = (name, to) => [{ find: new RegExp(`^${name}$`), replacement: to }, { find: new RegExp(`^${name}/(.*)$`), replacement: `${to}/$1` }]
const server = await createServer({
  configFile: false, root: tmp, logLevel: 'warn', plugins: [react(), tailwindcss()],
  resolve: { alias: [
    { find: /^@kirocrew\/app-sdk\/ui$/, replacement: path.join(site, 'src/components/ui.tsx') },
    { find: /^@kirocrew\/app-sdk$/, replacement: path.join(tmp, 'app-sdk.js') },
    { find: /^@host\//, replacement: path.join(site, 'src') + '/' },
    ...alias('react', path.join(nm, 'react')), ...alias('react-dom', path.join(nm, 'react-dom')),
  ] },
  server: { port: 5291, strictPort: true, host: '127.0.0.1', fs: { allow: [repo, site, tmp] } },
})
await server.listen()
const browser = await (pw.chromium || pw.default.chromium).launch({ executablePath: process.env.CHROMIUM_PATH || undefined })
const errors = []
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce', locale: 'en-US' })
  page.on('pageerror', (e) => errors.push(e.message))
  // The page scrolls inside the app's own container: grow the viewport to fit it.
  const shoot = async (name) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    const h = await page.evaluate(() => Math.max(...[...document.querySelectorAll('#root *')].map((e) => e.scrollHeight)))
    await page.setViewportSize({ width: 1440, height: Math.max(900, h) })
    await page.screenshot({ path: path.join(process.env.RSI_OUT || here, `${name}.png`) })
  }
  await page.goto('http://127.0.0.1:5291/index.html')
  const cards = page.getByTestId('proposal-card')
  await cards.first().waitFor({ timeout: 30000 })
  // Keyboard: a focused decision button is pressed with Enter.
  const doBtn = cards.first().getByRole('button', { name: '做', exact: true })
  await doBtn.focus()
  await page.keyboard.press('Enter')
  if ((await doBtn.getAttribute('aria-pressed')) !== 'true') errors.push('Enter did not press 做')
  await page.mouse.move(0, 0)
  await shoot('board')
  const pc = page.getByTestId('prompt-change-card')
  await pc.locator('summary').click()
  await pc.screenshot({ path: process.env.RSI_PROMPT_CHANGE ? `${process.env.RSI_PROMPT_CHANGE}.png` : path.join(here, 'prompt-change.png') })
  await pc.locator('summary').click()
  await page.getByRole('tab', { name: 'Signals' }).click()
  await page.getByTestId('signal-row').first().waitFor()
  await shoot('signals')
} finally {
  await browser.close()
  await server.close()
  fs.rmSync(tmp, { recursive: true, force: true })
}
if (errors.length) { console.error(errors.join('\n')); process.exit(1) }
console.log('wrote board.png, prompt-change.png, signals.png')
