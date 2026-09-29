// Capture the R0 screenshot baseline from a THROWAWAY seeded KiroCrew gateway.
//
// Never point this at a live gateway. Boot one the way KiroCrew's
// scripts/gui-user-test/boot.sh does (seed_home.py --fixture rich, fake ACP
// backend, isolated KIROCREW_HOME), then run from KiroCrew's website/ dir:
//
//   BASE_URL=http://127.0.0.1:<port> TOKEN=<one-time token> KC_SHA=<commit> \
//     OUT=<this repo>/baseline/screenshots node <this repo>/baseline/shoot.mjs
import { chromium } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'

const { BASE_URL, TOKEN, KC_SHA, OUT } = process.env
if (!BASE_URL || !TOKEN || !KC_SHA || !OUT) throw new Error('BASE_URL, TOKEN, KC_SHA and OUT are required')
const viewport = { width: 1440, height: 900 }

// BASE_URL must use host `localhost`: app routes redirect there and the
// session cookie is host-bound. page -> [path, text proving it rendered, clip]
const SIDEBAR = { x: 0, y: 0, width: 560, height: 900 }
const PAGES = [
  ['chat', '/chat/dashboard:coder-demo', 'Fix empty-glob crash', null],
  ['sidebar-folders', '/chat/dashboard:coder-demo', 'Demos', SIDEBAR],
  ['artifacts', '/artifacts', 'Your Artifacts', null],
  ['apps', '/apps/library', 'Manage your installed apps', null],
  ['slack-radar', '/apps/slack-radar', 'Needs you', null],
  ['issue-radar', '/apps/issue-radar', 'Issue Radar', null],
  ['settings', '/settings', 'System health', null],
]

const browser = await chromium.launch()
const context = await browser.newContext({ viewport, locale: 'en-US', colorScheme: 'light' })
const page = await context.newPage()
await page.goto(`${BASE_URL}/?token=${encodeURIComponent(TOKEN)}`, { waitUntil: 'load' })
await page.evaluate(() => {
  localStorage.setItem('mc-onboarded', '1')
  localStorage.setItem('mc-crewmates-onboarded', '1')
})

const shots = []
for (const [name, path, ready, clip] of PAGES) {
  await page.goto(`${BASE_URL}${path}`, { waitUntil: 'load' })
  await page.getByText(ready).first().waitFor({ timeout: 15000 })
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {})
  await page.waitForTimeout(1500)
  const file = `${name}.png`
  await page.screenshot({ path: join(OUT, file), ...(clip ? { clip } : {}) })
  shots.push({ page: name, path, file })
}
await browser.close()

const manifest = { kirocrew_commit: KC_SHA, viewport, data: 'KiroCrew seed fixture "rich" (throwaway home)', shots }
writeFileSync(join(OUT, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n')
console.log(`wrote ${shots.length} screenshots`)
