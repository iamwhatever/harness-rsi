// Run by test_ui.py beside the fakes in tests/ui/fakes. Boots the real page (hooks, effects, promises)
// and checks (1) ?demo=1 shows the fixtures on every tab while the plain page shows only the backend's
// data, and (2) every word on screen comes from ui/strings.mjs, in English, Chinese and a marker locale.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { createElement as h, __settle } from 'react'
import Page, { HarnessRsi, TABS } from './ui/index.mjs'
import { checklist, lastRound, nextLine, roundCost } from './ui/home.mjs'
import { leftToday, needsQueue } from './ui/needs.mjs'
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
  const trees = { home: await __settle(render, true) }
  for (const tab of TABS.slice(1)) trees[tab] = await tabTo(render, trees.home, tab)
  await tabTo(render, trees.home, 'home')
  return trees
}

// (1) Demo mode: the installed page with ?demo=1 renders the fixtures on every tab.
globalThis.location = { search: '?demo=1' }
globalThis.__api = { get: async () => { throw new Error('demo mode must not call the backend') }, post: async () => { throw new Error('no') } }
const page = () => expand(h(Page))
const demo = await everyTab(page)
assert.equal(TABS[0], 'home', 'Home is the first tab')
assert.equal(byTest(demo.home, 'demo-note').length, 1)
assert.equal(byTest(demo.home, 'proposal-card').length, 0, 'the page opens on Home, not the board')
assert.deepEqual(byTest(demo.home, 'setup-step').map((n) => n.props['data-key']), ['slack', 'github', 'chats', 'schedule', 'trust'])
assert.deepEqual(byTest(demo.home, 'setup-step').map((n) => n.props['data-ok']), ['false', 'true', 'true', 'false', 'null'])
assert.equal(byTest(demo.home, 'home-switch').length, 3, 'the three schedule switches show as badges')
// One primary action, Run round, with what it does and the last rounds' average.
const prim = all(demo.home).filter((n) => n.type === 'button' && n.props['data-primary'])
assert.deepEqual(prim.map((n) => n.props['data-testid']), ['round-arm'])
assert.equal(byTest(demo.home, 'round-cost')[0].children.filter((c) => typeof c === 'string').join(''), 'Last 2 round(s) took 55 min on average; cost: unknown (not measured yet). ')
assert.equal(byTest(demo.home, 'home-details').length, 1, 'signals, runs and raw A/B are folded on Home')
assert.equal(byTest(demo.home, 'signal-row').length, signals.length)
assert.equal(byTest(demo.home, 'home-raw-ab').length, 1)
assert.ok(byTest(demo.home, 'src').length >= 10, 'every number names its source')
// A fix link moves to the tab that fixes the step; the other tabs stay reachable after Home.
{
  const fixes = byTest(demo.home, 'setup-fix')
  assert.deepEqual(fixes.map((n) => n.props['data-to']), ['settings', 'settings', 'settings'])
  fixes[0].props.onClick()
  const there = await __settle(page)
  assert.equal(byTest(there, 'slack-configured').length, 1, 'Fix in Settings opens Settings')
  await tabTo(page, there, 'home')
}
// Signals, Rounds and Team are tabs no more: they fold into Home's Details; Board and Prompt changes stay until Needs you lands.
assert.deepEqual(TABS, ['home', 'work', 'board', 'prompts', 'settings'])
assert.equal(byTest(demo.board, 'proposal-card').length, proposals.length)
assert.equal(byTest(demo.home, 'run-row').length, 2)
assert.equal(byTest(demo.home, 'details-refresh').length, 1, 'Refresh signals is in Details')
assert.equal(byTest(demo.home, 'slack-off').length, 1)
assert.equal(byTest(demo.home, 'round-job').length, 1)
assert.equal(byTest(demo.home, 'regress').length, 1)
assert.equal(byTest(demo.prompts, 'prompt-change-card').length, 1)
assert.equal(byTest(demo.settings, 'slack-configured').length, 1)
const dteam = byTest(demo.home, 'details-team')
assert.equal(dteam.length, 1, 'the Team tree is in Details')
assert.equal(byTest(dteam, 'team-note').length, 1, 'the Team tree says the data is self-reported')
assert.equal(byTest(dteam, 'team-item').length, 8)
assert.deepEqual(byTest(dteam, 'need').map((n) => n.props['data-why']), ['question', 'blocked', 'merge'])
assert.equal(byTest(dteam, 'team-lane').length, 3, 'two lanes under the lead, one loose')
assert.equal(byTest(dteam, 'stale-reporter').length, 2)
assert.ok(byTest(dteam, 'session-link').every((a) => a.props.href.startsWith('/chat?slot=')))
assert.ok(byTest(demo.board, 'score').length >= 1, 'demo cards show the fixture judge scores')
assert.ok(byTest(demo.board, 'sources')[0].children.some((a) => a?.props?.href === signals[0].links[0]), 'a card links its signal source')
// Demo Dispatch: Do with auto-dispatch off says how to start it; Dispatch all Do starts the Do cards inline, within the cap.
{
  const dsrc = demoSource()
  const page2 = () => expand(h(HarnessRsi, { src: dsrc, demo: true }))
  let tree = await tabTo(page2, await __settle(page2, true), 'board')
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
const liveHome = await __settle(page, true)
assert.ok(byTest(liveHome, 'setup-step').every((n) => n.props['data-ok'] !== 'true'), 'an empty backend has no step done')
assert.ok(reads.includes('/api/apps/harness-rsi/schedule'))
assert.equal(byTest(liveHome, 'setup-refresh').length, 1, 'the GitHub step fixes itself with Refresh signals, no Signals tab')
const live = await tabTo(page, liveHome, 'board')
assert.equal(byTest(live, 'demo-note').length, 0)
assert.equal(byTest(live, 'proposal-card').length, 0)
assert.equal(byTest(live, 'empty-state').length, 1)
assert.equal(byTest(liveHome, 'team-item').length, 0, 'an empty backend is an empty Team tree, never the fixtures')
assert.equal(byTest(await tabTo(page, live, 'work'), 'work-row').length, 0, 'an empty backend is an empty Work tab, never the fixtures')
assert.ok(reads.includes('/api/apps/harness-rsi/team'))
assert.ok(reads.includes('/api/apps/harness-rsi/proposals'))
// A failing backend is an error notice, not a blank page or a silent fallback to fixtures.
globalThis.__api = { get: async () => { throw new Error('502 Bad Gateway') }, post: async () => ({}) }
const broken = await tabTo(page, await __settle(page, true), 'board')
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
// Text in <code> (routes, script and metric names) is an identifier, not prose.
const noCode = (ns) => ns.filter((n) => !(n && n.type === 'code')).map((n) => (n && typeof n === 'object' ? { ...n, children: noCode(n.children) } : n))
const shown = (tree) => all(tree).flatMap((n) => [...n.children.filter((c) => typeof c === 'string' || typeof c === 'number').map(String),
  ...ATTRS.map((a) => n.props[a]).filter((x) => typeof x === 'string')])
// Innermost first, since a table string can hold another one ({head} is a translated verdict).
const unmark = (s) => (/«[^«»]*»/.test(s) ? unmark(s.replace(/«[^«»]*»/g, '')) : s)
const strip = (s) => dataByLength.reduce((acc, d) => acc.split(d).join(''), s)

// The marker locale wraps every table string in «»; anything left after removing those and the data is untabled.
TABLE.qa = Object.fromEntries(Object.entries(TABLE.en).map(([k, v]) => [k, v.split(',').map((x) => `«${x}»`).join(',')]))
for (const lang of ['qa', 'en', 'zh']) {
  setLang(lang)
  const trees = await everyTab(() => noCode(expand(h(HarnessRsi, { src, demo: true }))))
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
  const oldHome = await __settle(page, true)
  assert.equal(byTest(oldHome, 'ux-v2').length, 0)
  assert.equal(byTest(oldHome, 'home-status').length, 1)
  const old = await tabTo(page, oldHome, 'board')
  assert.equal(byTest(old, 'proposal-card').length, proposals.length)
  // Every v2 word is in the table too.
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
// (4) Home on live states: next round, last round, cost, and each step's real state.
{
  setLang('en')
  const conf = { round_enabled: true, regress_enabled: false, score_enabled: true, weekday: 0, hour: 9, kirocrew_dir: '/kc' }
  assert.equal(nextLine({ schedule: conf, next_round_at: null }), 'Next round: none, the weekly round is off. Turn it on in Settings.')
  assert.equal(nextLine({ schedule: conf, next_round_at: '2026-10-05T09:00:00+00:00', read_at: '2026-10-05T09:30:00+00:00' }), 'Next round: due now; the next hourly check starts it.')
  assert.match(nextLine({ schedule: conf, next_round_at: '2026-10-12T09:00:00+00:00', read_at: '2026-10-05T09:30:00+00:00' }), /^Next round: .+ \(the first hourly check after it starts it\)\.$/)
  assert.equal(roundCost(null), 'Time and cost: unknown until a round finishes.')
  assert.equal(roundCost({ n: 3, avg_duration_s: 5400, avg_cost: 1.25 }), 'Last 3 round(s) took 1 h 30 min on average; cost: 1.25 credits.')
  assert.equal(lastRound(null, [{ kind: 'regress', end: 'x' }]), null)
  assert.deepEqual(lastRound(null, [{ kind: 'regress' }, { kind: 'manual_round', end: 'e', signals: 4, cards: 3, error: '' }]),
    { n: null, s: 4, p: 3, at: 'e', e: '', route: '/schedule' })
  const steps = checklist({ settings: { command_set: true, channels: ['C1'], window_days: 7 }, sched: { schedule: conf, next_round_at: null },
    job: { finished_at: 5, error: 'github: OSError', rows: null }, disp: { trust_dispatched: true },
    round: { notes: ['sessions: OSError'] }, signals: [{ source: 'session:owner' }] })
  assert.deepEqual(steps.map((x) => [x.key, x.ok]), [['slack', true], ['github', false], ['chats', false], ['schedule', true], ['trust', null]])
  assert.match(steps[1].text, /^GitHub: failed at .+ \(github: OSError\)$/)
  assert.equal(steps[2].text, 'The last round could not read your chats (sessions: OSError).')
  assert.match(steps[4].text, /^Risk: a trusted chat runs tools without asking you/)
}
// (5) Needs you: one queue, right after Home, of everything waiting on the owner; each item has why, evidence and one action.
{
  setLang('en')
  assert.equal(TABS[1], 'needs', 'Needs you comes right after Home')
  const nsrc = demoSource()
  const render = () => expand(h(HarnessRsi, { src: nsrc, demo: true }))
  let tree = await tabTo(render, await __settle(render, true), 'needs')
  const kinds = (tr) => byTest(tr, 'need-item').map((n) => n.props['data-kind'])
  const item = (tr, kind) => byTest(tr, 'need-item').find((n) => n.props['data-kind'] === kind)
  const text = (n) => all([n]).flatMap((x) => x.children.filter((c) => typeof c === 'string' || typeof c === 'number')).join('')
  assert.deepEqual(kinds(tree), ['pick', 'start', 'prompt', 'question', 'blocked', 'merge'])
  for (const n of byTest(tree, 'need-item')) {
    assert.equal(all([n]).filter((x) => x.props['data-testid'] === 'need-action').length, 1, `${n.props['data-kind']}: one action`)
    assert.equal(all([n]).filter((x) => x.props['data-primary']).length <= 1, true, `${n.props['data-kind']}: at most one primary button`)
    assert.equal(all([n]).filter((x) => x.props['data-testid'] === 'need-why').length, 1, `${n.props['data-kind']}: says why`)
    assert.ok(all([n]).some((x) => x.props['data-testid'] === 'src'), `${n.props['data-kind']}: names its source`)
  }
  // A pick offers Do / Skip / Later; Start says how many are left today (daily_cap - used_today from GET /dispatch).
  assert.deepEqual(['need-action', 'need-skip', 'need-later'].map((k) => all([item(tree, 'pick')]).filter((x) => x.props['data-testid'] === k).length), [1, 1, 1])
  assert.match(text(item(tree, 'start')), /2 left today\./)
  // A prompt change shows the A/B numbers with when it ran and the command.
  const ab = all([item(tree, 'prompt')]).find((x) => x.props['data-testid'] === 'ab-source')
  assert.match(text(ab), /^A\/B: offline replay of rounds 3, 4, 3 runs each; ran .+ with python3 crew\/ab\.py /)
  assert.equal(all([ab]).find((x) => x.type === 'code').children.join(''), promptChange.ab.command)
  // Team items: question and blocked open their chat; merge links the PR on the one allowlisted repo.
  const href = (kind) => all([item(tree, kind)]).find((x) => x.props['data-testid'] === 'need-action').props.href
  assert.equal(href('question'), '/chat?slot=chat-fake-lane-exam')
  assert.equal(href('merge'), 'https://github.com/example-org/example-repo/pull/101')
  assert.match(text(item(tree, 'question')), /Two exams flake; retire them\?/, 'a question shows the worker summary')
  // Do on a pick moves it to Start; Start opens the chat and the card leaves, and the count left drops.
  all([item(tree, 'pick')]).find((x) => x.props['data-testid'] === 'need-action').props.onClick()
  tree = await __settle(render)
  assert.deepEqual(kinds(tree), ['start', 'start', 'prompt', 'question', 'blocked', 'merge'])
  const startId = item(tree, 'start').props['data-id']
  all([item(tree, 'start')]).find((x) => x.props['data-testid'] === 'need-action').props.onClick()
  tree = await __settle(render)
  assert.ok(!byTest(tree, 'need-item').some((n) => n.props['data-id'] === startId), 'a started card leaves the queue')
  assert.match(text(item(tree, 'start')), /1 left today\./)
  // Switch to B applies the change and it leaves the queue.
  all([item(tree, 'prompt')]).find((x) => x.props['data-testid'] === 'need-action').props.onClick()
  tree = await __settle(render)
  assert.ok(!kinds(tree).includes('prompt'))
  // The queue on its own: an error row is a Start with its reason; an older A/B without ran_at says so; no used_today = unknown.
  assert.equal(leftToday({ daily_cap: 2, used_today: 5 }), 0)
  assert.equal(leftToday({ daily_cap: 2 }), null)
  const q = needsQueue({ proposals: [{ ...proposals[2], decision: 'do' }], out: { dispatches: [{ card_id: proposals[2].id, state: 'error', error: 'boom', at: 'x' }] },
    disp: { daily_cap: 2, used_today: 2 }, changes: [{ ...promptChange, status: 'skip' }], team: null })
  assert.deepEqual(q.map((x) => [x.kind, x.left, x.d.error]), [['start', 0, 'boom']])
  assert.equal(needsQueue({ proposals: [{ ...proposals[2], decision: 'do' }], out: { dispatches: [{ card_id: proposals[2].id, state: 'dispatched' }] } }).length, 0)
  const { abSource } = await import('./ui/needs.mjs')
  assert.match(abSource({ rounds: [1], reps: 2 }).filter((x) => typeof x === 'string').join(''), /run time not recorded; script/)
  // An empty backend is an empty queue that names the next round.
  globalThis.location = { search: '' }
  globalThis.__api = { get: async () => none, post: async () => ({ ok: true }) }
  const empty = await tabTo(page, await __settle(page, true), 'needs')
  assert.equal(byTest(empty, 'need-item').length, 0)
  assert.equal(text(byTest(empty, 'needs-empty')[0]), 'Nothing needs youNext round: none, the weekly round is off. Turn it on in Settings.')
}
console.log('page ok')
