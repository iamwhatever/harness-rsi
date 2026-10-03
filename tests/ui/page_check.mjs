// Run by test_ui.py beside the fakes in tests/ui/fakes. Boots the real page (hooks, effects, promises)
// and checks (1) ?demo=1 shows the fixtures on every tab while the plain page shows only the backend's
// data, and (2) every word on screen comes from ui/strings.mjs, in English, Chinese and a marker locale.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { createElement as h, __settle } from 'react'
import Page, { HarnessRsi, TABS } from './ui/index.mjs'
import { TABLE, setLang } from './ui/strings.mjs'
import { demoSource, promptChange } from './ui/fake-data.mjs'

const fixture = (f) => JSON.parse(fs.readFileSync(`fixtures/${f}.json`, 'utf8'))
const [proposals, signals, outcomes] = ['proposals', 'signals', 'outcomes'].map(fixture)
const expand = (n) => (Array.isArray(n) ? n.flatMap(expand) : n == null || typeof n !== 'object' ? [n]
  : typeof n.type === 'function' ? expand(n.type(n.props)) : [{ ...n, children: expand(n.props.children) }])
const all = (nodes) => nodes.filter((n) => n && typeof n === 'object').flatMap((n) => [n, ...all(n.children)])
const byTest = (tree, id) => all(tree).filter((n) => n.props['data-testid'] === id)
const tabTo = async (render, tree, tab) => {
  all(tree).find((n) => n.props['data-tab'] === tab).props.onClick()
  return __settle(render)
}
/** Every tab's tree, after the page has settled on each. */
async function everyTab(render) {
  const trees = { board: await __settle(render, true) }
  for (const tab of TABS.slice(1)) trees[tab] = await tabTo(render, trees.board, tab)
  await tabTo(render, trees.board, 'board')
  return trees
}

// (1) Demo mode: the installed page with ?demo=1 renders the fixtures on every tab.
globalThis.location = { search: '?demo=1' }
globalThis.__api = { get: async () => { throw new Error('demo mode must not call the backend') }, post: async () => { throw new Error('no') } }
const page = () => expand(h(Page))
const demo = await everyTab(page)
assert.equal(byTest(demo.board, 'demo-note').length, 1)
assert.equal(byTest(demo.board, 'proposal-card').length, proposals.length)
assert.equal(byTest(demo.signals, 'signal-row').length, signals.length)
assert.equal(byTest(demo.rounds, 'run-row').length, 1)
assert.equal(byTest(demo.prompts, 'prompt-change-card').length, 1)
assert.equal(byTest(demo.settings, 'slack-configured').length, 1)
assert.ok(byTest(demo.board, 'score').length >= 1, 'demo cards show the fixture judge scores')
assert.ok(byTest(demo.board, 'sources')[0].children.some((a) => a?.props?.href === signals[0].links[0]), 'a card links its signal source')

// Without the flag the page reads the backend only: an empty backend is an empty board, never the fixtures.
globalThis.location = { search: '' }
const reads = []
const none = { proposals: [], signals: [], changes: [], settings: { command_set: false, channels: [], window_days: 14, workspace_url: '' },
  schedule: { schedule: { round_enabled: false, regress_enabled: false, score_enabled: false, weekday: 0, hour: 9, kirocrew_dir: '' }, runs: [] },
  dispatch: { auto_dispatch: false, repos: [], daily_cap: 2 }, github: null, round: null, runs: [], outcomes: [] }
globalThis.__api = { get: async (p) => { reads.push(p); return none }, post: async () => ({ ok: true }) }
const live = await __settle(page, true)
assert.equal(byTest(live, 'demo-note').length, 0)
assert.equal(byTest(live, 'proposal-card').length, 0)
assert.equal(byTest(live, 'empty-state').length, 1)
assert.ok(reads.includes('/api/apps/harness-rsi/proposals'))
// A failing backend is an error notice, not a blank page or a silent fallback to fixtures.
globalThis.__api = { get: async () => { throw new Error('502 Bad Gateway') }, post: async () => ({}) }
const broken = await __settle(page, true)
assert.equal(byTest(broken, 'load-error').length, 1)
assert.equal(byTest(broken, 'proposal-card').length, 0)

// (2) One string table. Data values may appear as they are; everything else must come from the table.
const data = new Set()
const collect = (v, k) => {
  if (k) data.add(k)
  if (typeof v === 'string') { data.add(v); if (/^[0-9a-f]{40}$/.test(v)) data.add(v.slice(0, 7)); if (v.includes('#')) data.add(v.split('/').pop()) }
  else if (v && typeof v === 'object') for (const [kk, x] of Object.entries(v)) collect(x, Array.isArray(v) ? null : kk)
}
const src = demoSource()
collect([proposals, signals, outcomes, promptChange, await src.settings(), await src.dispatchConf(), await src.schedule()])
const dataByLength = [...data].filter((x) => x.length > 1).sort((a, b) => b.length - a.length)
const ATTRS = ['aria-label', 'alt', 'placeholder', 'title']
const shown = (tree) => all(tree).flatMap((n) => [...n.children.filter((c) => typeof c === 'string' || typeof c === 'number').map(String),
  ...ATTRS.map((a) => n.props[a]).filter((x) => typeof x === 'string')])
// Innermost first, since a table string can hold another one ({head} is a translated verdict).
const unmark = (s) => (/«[^«»]*»/.test(s) ? unmark(s.replace(/«[^«»]*»/g, '')) : s)
const strip = (s) => dataByLength.reduce((acc, d) => acc.split(d).join(''), s)

// The marker locale wraps every table string in «»; anything left after removing those and the data is untabled.
TABLE.qa = Object.fromEntries(Object.entries(TABLE.en).map(([k, v]) => [k, v.split(',').map((x) => `«${x}»`).join(',')]))
for (const lang of ['qa', 'en', 'zh']) {
  setLang(lang)
  const trees = await everyTab(() => expand(h(HarnessRsi, { src, demo: true })))
  for (const [tab, tree] of Object.entries(trees)) {
    for (const s of shown(tree)) {
      const rest = strip(lang === 'qa' ? unmark(s) : s)
      if (lang === 'qa') assert.match(rest, /^[\s\d.,:;/·→—#%()+\-…]*$/u, `${tab}: "${s}" is shown without the string table`)
      // English shows no Chinese; Chinese shows no English sentence (product names and ids may stay).
      if (lang === 'en') assert.doesNotMatch(rest, /\p{Script=Han}/u, `${tab}: "${s}" mixes Chinese into English`)
      if (lang === 'zh') assert.doesNotMatch(rest.replace(/Slack|GitHub|KiroCrew|MCP|PR|A\/B|ID|CI|fork|Harness|demo|\{\w+\}/g, ''),
        /[A-Za-z]{3,}/, `${tab}: "${s}" mixes English into Chinese`)
    }
  }
}
// zh is complete: the same keys, every value set, and the {slots} match.
const slots = (v) => [...v.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join()
assert.deepEqual(Object.keys(TABLE.zh).sort(), Object.keys(TABLE.en).sort())
for (const [k, v] of Object.entries(TABLE.en)) {
  assert.ok(TABLE.zh[k], `zh.${k} is empty`)
  assert.equal(slots(TABLE.zh[k]), slots(v), `zh.${k} slots`)
  assert.doesNotMatch(v, /\p{Script=Han}/u, `en.${k} has Chinese`)
}
assert.equal(TABLE.zh.days.split(',').length, 7)
console.log('page ok')
