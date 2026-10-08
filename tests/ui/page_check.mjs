// Run by test_ui.py beside the fakes in tests/ui/fakes. Boots the real page (hooks, effects, promises)
// and checks (1) ?demo=1 shows the fixtures on every tab while the plain page shows only the backend's
// data, and (2) every word on screen comes from ui/strings.mjs, in English, Chinese and a marker locale.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { createElement as h, __settle } from 'react'
import Page, { HarnessRsi, TABS } from './ui/index.mjs'
import { TABLE, setLang } from './ui/strings.mjs'
import { demoSource, promptChange, team } from './ui/fake-data.mjs'

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
assert.equal(byTest(demo.team, 'team-note').length, 1, 'the Team tab says the data is self-reported')
assert.equal(byTest(demo.team, 'team-item').length, 8)
assert.deepEqual(byTest(demo.team, 'need').map((n) => n.props['data-why']), ['question', 'blocked', 'merge'])
assert.equal(byTest(demo.team, 'team-lane').length, 3, 'two lanes under the lead, one loose')
assert.equal(byTest(demo.team, 'stale-reporter').length, 2)
assert.ok(byTest(demo.team, 'session-link').every((a) => a.props.href.startsWith('/chat?slot=')))
assert.ok(byTest(demo.board, 'score').length >= 1, 'demo cards show the fixture judge scores')
assert.ok(byTest(demo.board, 'sources')[0].children.some((a) => a?.props?.href === signals[0].links[0]), 'a card links its signal source')
// Demo Dispatch: Do with auto-dispatch off says how to start it; Dispatch all Do starts the Do cards inline, within the cap.
{
  const dsrc = demoSource()
  const page2 = () => expand(h(HarnessRsi, { src: dsrc, demo: true }))
  let tree = await __settle(page2, true)
  const fresh = proposals.find((p) => !p.decision)
  const card = byTest(tree, 'proposal-card').find((c) => c.props['data-id'] === fresh.id)
  const doBtn = all([card]).find((n) => n.props['data-decision'] === 'do')
  doBtn.props.onClick()
  tree = await __settle(page2)
  const noteText = (t) => byTest(t, 'note').map((n) => n.children.join('')).join('')
  assert.equal(noteText(tree), 'Saved. Not started: use Dispatch or turn on auto-dispatch.')
  const want = proposals.filter((p) => p.decision === 'do').length + 1
  assert.equal(byTest(tree, 'dispatch-btn').length, want)
  byTest(tree, 'dispatch-all')[0].props.onClick()
  tree = await __settle(page2)
  const got = byTest(tree, 'dispatch-result').map((n) => n.props['data-result'])
  assert.deepEqual(got.sort(), ['started', 'started', ...Array(Math.max(0, want - 2)).fill('over_cap')].sort())
  assert.ok(byTest(tree, 'dispatch-result').filter((n) => n.props['data-result'] === 'started')
    .every((n) => all([n]).some((a) => a.type === 'a' && a.props.href.startsWith('/chat?slot=rsi-demo-'))))
  assert.match(noteText(tree), /^Dispatch: 2 of \d+ started$/)
}

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
const liveTeam = await tabTo(page, live, 'team')
assert.equal(byTest(liveTeam, 'team-item').length, 0, 'an empty backend is an empty Team tab, never the fixtures')
assert.ok(reads.includes('/api/apps/harness-rsi/team'))
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
collect([proposals, signals, outcomes, promptChange, team, await src.settings(), await src.dispatchConf(), await src.schedule()])
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
// (3) UX v2 mockup: only with ?demo=1&ux=v2, on the fixtures; ux=v2 alone or demo alone keeps the current page.
{
  const { HarnessRsiV2, VIEWS } = await import('./ui/v2.mjs')
  setLang('en')
  const noApi = { get: async () => { throw new Error('the v2 mockup must not call the backend') }, post: async () => { throw new Error('no') } }
  const views = async (render) => {
    const trees = { home: await __settle(render, true) }
    for (const v of VIEWS.slice(1)) trees[v] = await tabTo(render, trees.home, v)
    await tabTo(render, trees.home, 'home')
    return trees
  }
  globalThis.__api = noApi
  globalThis.location = { search: '?demo=1&ux=v2' }
  const v2 = await views(page)
  assert.equal(byTest(v2.home, 'ux-v2').length, 1)
  assert.equal(byTest(v2.home, 'demo-note').length, 1)
  assert.equal(byTest(v2.home, 'proposal-card').length, 0, 'the v2 mockup is not the current board')
  assert.equal(byTest(v2.home, 'setup-step').length, 4)
  assert.equal(byTest(v2.home, 'primary').length, 1, 'Home has one primary action')
  assert.equal(byTest(v2.home, 'v2-details').length, 1, 'Signals, runs and raw A/B are folded on Home')
  assert.equal(byTest(v2.home, 'signal-row').length, signals.length)
  const kinds = (tree) => byTest(tree, 'need-item').map((n) => n.props['data-kind'])
  assert.deepEqual(kinds(v2.needs), ['pick', 'start', 'prompt', 'question', 'blocked', 'merge'])
  assert.ok(byTest(v2.needs, 'need-item').every((n) => byTest([n], 'need-action').length === 1), 'every item has one action')
  assert.ok(byTest(v2.needs, 'cost-note').length >= byTest(v2.needs, 'need-action').length, 'every action says what it costs')
  assert.match(byTest(v2.needs, 'ab-source')[0].children.flat().join(''), /offline replay/)
  assert.equal(byTest(v2.work, 'work-row').length, 3)
  assert.ok(byTest(v2.work, 'work-row').every((r) => byTest([r], 'work-step').length === 6))
  assert.ok(byTest(v2.work, 'work-step').filter((s) => s.props['data-step'] === 'ci').every((s) => s.props['data-done'] === false), 'CI is a gap')
  assert.equal(byTest(v2.settings, 'v2-setting').length, 11)
  // Each card lives in one place: picking moves it from Needs you to Work; starting fills its chat step.
  {
    const dsrc = demoSource()
    const render = () => expand(h(HarnessRsiV2, { src: dsrc }))
    let tree = await tabTo(render, await __settle(render, true), 'needs')
    const pickId = byTest(tree, 'need-item').find((n) => n.props['data-kind'] === 'pick').props['data-id']
    all(byTest(tree, 'need-item').filter((n) => n.props['data-kind'] === 'pick')).find((n) => n.props['data-testid'] === 'need-action').props.onClick()
    tree = await __settle(render)
    assert.ok(!kinds(tree).includes('pick'))
    const startId = byTest(tree, 'need-item').find((n) => n.props['data-kind'] === 'start').props['data-id']
    all(byTest(tree, 'need-item').filter((n) => n.props['data-kind'] === 'start')).find((n) => n.props['data-testid'] === 'need-action').props.onClick()
    tree = await __settle(render)
    assert.ok(!kinds(tree).includes('start'))
    tree = await tabTo(render, tree, 'work')
    const step = (id, k) => byTest(byTest(tree, 'work-row').filter((r) => r.props['data-id'] === id), 'work-step').find((s) => s.props['data-step'] === k)
    assert.equal(byTest(tree, 'work-row').length, 4)
    assert.equal(step(pickId, 'merged').props['data-done'], true)
    assert.equal(step(startId, 'chat').props['data-done'], true)
    assert.ok(all([step(startId, 'chat')]).some((a) => a.type === 'a' && a.props.href.startsWith('/chat?slot=rsi-demo-')))
  }
  // ux=v2 without demo is the current page over the backend; demo without ux=v2 is the current demo page.
  globalThis.location = { search: '?ux=v2' }
  globalThis.__api = { get: async () => none, post: async () => ({ ok: true }) }
  const plain = await __settle(page, true)
  assert.equal(byTest(plain, 'ux-v2').length, 0)
  assert.equal(byTest(plain, 'demo-note').length, 0)
  globalThis.location = { search: '?demo=1' }
  globalThis.__api = noApi
  const old = await __settle(page, true)
  assert.equal(byTest(old, 'ux-v2').length, 0)
  assert.equal(byTest(old, 'proposal-card').length, proposals.length)
  // Every v2 word is in the table too. Text in <code> (routes, script and metric names) is an identifier, not prose.
  const noCode = (ns) => ns.filter((n) => !(n && n.type === 'code')).map((n) => (n && typeof n === 'object' ? { ...n, children: noCode(n.children) } : n))
  for (const lang of ['qa', 'en', 'zh']) {
    setLang(lang)
    const vsrc = demoSource()
    const trees = await views(() => noCode(expand(h(HarnessRsiV2, { src: vsrc }))))
    for (const [view, tree] of Object.entries(trees)) {
      for (const s of shown(tree)) {
        const rest = strip(lang === 'qa' ? unmark(s) : s)
        if (lang === 'qa') assert.match(rest, /^[\s\d.,:;/·→—#%()+\-…]*$/u, `v2 ${view}: "${s}" is shown without the string table`)
        if (lang === 'en') assert.doesNotMatch(rest, /\p{Script=Han}/u, `v2 ${view}: "${s}" mixes Chinese into English`)
        if (lang === 'zh') assert.doesNotMatch(rest.replace(/Slack|GitHub|KiroCrew|MCP|PR|A\/B|ID|CI|fork|Harness|demo|\{\w+\}/g, ''),
          /[A-Za-z]{3,}/, `v2 ${view}: "${s}" mixes English into Chinese`)
      }
    }
  }
  setLang('en')
}
console.log('page ok')
