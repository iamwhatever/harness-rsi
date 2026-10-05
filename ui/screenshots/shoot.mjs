// Render ui/screenshots/*.png from the FAKE fixtures (demoSource) with the host's real UI components,
// theme and i18n; nothing talks to a gateway. Needs a KiroCrew website checkout:
//   KIROCREW_WEBSITE=/path/to/KiroCrew/website node ui/screenshots/shoot.mjs
// RSI_LANG=en|zh (host locale), RSI_W=width (default 1440), RSI_OUT=dir: every tab as <tab>-<lang>-<width>.png.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'

const here = path.dirname(fileURLToPath(import.meta.url))
const repo = path.resolve(here, '../..')
const site = process.env.KIROCREW_WEBSITE
if (!site) throw new Error('set KIROCREW_WEBSITE to a KiroCrew/website checkout')
const [LANG, W, OUT] = [process.env.RSI_LANG || 'en', Number(process.env.RSI_W || 1440), process.env.RSI_OUT || here]
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
// The page body never calls the backend hook; the stub keeps the host SDK barrel out but gives the host locale.
fs.writeFileSync(path.join(tmp, 'app-sdk.js'), "export const useAppApi = () => null\nexport { activeLocale } from '@host/i18n/format'\n")
fs.writeFileSync(path.join(tmp, 'harness.css'), '@import "@host/index.css";\n')
fs.writeFileSync(path.join(tmp, 'index.html'),
  `<!doctype html><html lang="${LANG}"><body><div id="root"></div><script type="module" src="./main.jsx"></script></body></html>`)
fs.writeFileSync(path.join(tmp, 'main.jsx'), `import { createRoot } from 'react-dom/client'
import { initI18n } from '@host/i18n/all'
import { activeLocale } from '@kirocrew/app-sdk'
import { HarnessRsi } from '${path.join(repo, 'ui/index.mjs')}'
import { pickLang, setLang } from '${path.join(repo, 'ui/strings.mjs')}'
import { demoSource } from '${path.join(repo, 'ui/fake-data.mjs')}'
import './harness.css'
await initI18n('${LANG}')
setLang(pickLang(activeLocale()))
document.documentElement.setAttribute('data-theme', 'dark')
const demo = demoSource(), src = { ...demo, load: async () => ({ ...(await demo.load()), images: { prop_bg_tasks: { before: '${before}' } } }) }
createRoot(document.getElementById('root')).render(<div className="flex flex-col h-screen bg-bg text-text"><HarnessRsi src={src} demo /></div>)
`)
// The host's stock (no edition) answer for its virtual modules, which the shared components import.
const stockEdition = { name: 'stock-edition', enforce: 'pre', resolveId: (id) => (id.startsWith('virtual:kirocrew-') ? `\0${id}` : null),
  load: (id) => (id.startsWith('\0virtual:kirocrew-') ? 'export default []\n' : null) }
const alias = (name, to) => [{ find: new RegExp(`^${name}$`), replacement: to }, { find: new RegExp(`^${name}/(.*)$`), replacement: `${to}/$1` }]
const server = await createServer({
  configFile: false, root: tmp, logLevel: 'warn', plugins: [react(), tailwindcss(), stockEdition], cacheDir: path.join(tmp, '.vite'),
  resolve: { alias: [
    { find: /^@kirocrew\/app-sdk\/ui$/, replacement: path.join(site, 'src/kirocrew-ui/index.ts') },
    { find: /^@kirocrew\/app-sdk$/, replacement: path.join(tmp, 'app-sdk.js') },
    { find: /^@host\//, replacement: path.join(site, 'src') + '/' },
    ...alias('react', path.join(nm, 'react')), ...alias('react-dom', path.join(nm, 'react-dom')), ...alias('lucide-react', path.join(nm, 'lucide-react')),
  ] },
  server: { port: 5291, strictPort: true, host: '127.0.0.1', fs: { allow: [repo, site, tmp] } },
})
await server.listen()
const browser = await (pw.chromium || pw.default.chromium).launch({ executablePath: process.env.CHROMIUM_PATH || undefined })
const errors = []
const TABS = { en: ['Board', 'Signals', 'Rounds', 'Prompt changes', 'Team', 'Settings'], zh: ['看板', '信号', '轮次', '提示词改动', '团队', '设置'] }[LANG]
try {
  const page = await browser.newPage({ viewport: { width: W, height: 900 }, reducedMotion: 'reduce', locale: LANG === 'zh' ? 'zh-CN' : 'en-US' })
  page.on('pageerror', (e) => errors.push(e.message))
  // The page scrolls inside the app's own container: grow the viewport to fit it.
  const shoot = async (name) => {
    await page.setViewportSize({ width: W, height: 900 })
    const h = await page.evaluate(() => Math.max(...[...document.querySelectorAll('#root *')].map((e) => e.scrollHeight)))
    await page.setViewportSize({ width: W, height: Math.max(900, h) })
    await page.mouse.move(0, 0)
    // Nothing may stick out past the viewport, except inside a horizontal scroller (the tables).
    const wide = await page.evaluate((w) => [...document.querySelectorAll('#root *')].filter((e) => e.getBoundingClientRect().right > w + 1
      && !e.closest('.overflow-x-auto')).map((e) => `${e.tagName}.${e.className}`).slice(0, 3), W)
    if (wide.length) errors.push(`${name}: wider than ${W}px: ${wide.join(' | ')}`)
    await page.screenshot({ path: path.join(OUT, `${name}-${LANG}-${W}.png`) })
  }
  await page.goto('http://127.0.0.1:5291/index.html')
  const cards = page.getByTestId('proposal-card')
  await cards.first().waitFor({ timeout: 30000 })
  // Keyboard: a focused decision button is pressed with Enter.
  const doBtn = cards.first().locator('[data-decision="do"]')
  await doBtn.focus()
  await page.keyboard.press('Enter')
  await page.waitForFunction(() => document.querySelector('[data-testid="proposal-card"] [data-decision="do"]')?.getAttribute('aria-pressed') === 'true')
    .catch(() => errors.push('Enter did not press Do'))
  await shoot('board')
  for (const [i, name] of ['signals', 'rounds', 'prompts', 'team', 'settings'].entries()) {
    await page.getByRole('radio', { name: new RegExp(`^${TABS[i + 1]}`) }).click()
    await page.getByTestId(`panel-${name}`).waitFor()
    if (name === 'prompts') await page.getByTestId('prompt-change-card').locator('summary').click()
    await shoot(name)
  }
} catch (e) { errors.push(String(e)) } finally {
  await browser.close()
  await server.close()
  fs.rmSync(tmp, { recursive: true, force: true })
}
if (errors.length) { console.error(errors.join('\n')); process.exit(1) }
console.log(`wrote 6 shots for ${LANG} at ${W}px to ${OUT}`)
