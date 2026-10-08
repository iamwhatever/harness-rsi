// Run by test_ui.py beside the fakes in tests/ui/fakes. The Work tab: one row per picked card through
// six steps (CI from GET /outcomes' `ci`), Team flags joined by worker chat or PR number, PRs with no card last.
import assert from 'node:assert/strict'
import { createElement as h, __settle } from 'react'
import { HarnessRsi, TABS } from './ui/index.mjs'
import { flags, steps, teamItems, workRows } from './ui/work.mjs'
import { setLang } from './ui/strings.mjs'
import { ci, demoSource, outcomes, proposals, team } from './ui/fake-data.mjs'

setLang('en')
const expand = (n) => (Array.isArray(n) ? n.flatMap(expand) : n == null || typeof n !== 'object' ? [n]
  : typeof n.type === 'function' ? expand(n.type(n.props)) : [{ ...n, children: expand(n.props.children) }])
const all = (nodes) => nodes.filter((n) => n && typeof n === 'object').flatMap((n) => [n, ...all(n.children)])
const byTest = (tree, id) => all(tree).filter((n) => n.props['data-testid'] === id)
const text = (n) => all([n]).flatMap((x) => x.children.filter((c) => typeof c === 'string')).join('')
const tabTo = async (render, tree, tab) => { all(tree).find((n) => n.props['data-tab'] === tab).props.onClick(); return __settle(render) }

assert.equal(TABS[1], 'work', 'Work is the tab after Home')
// Rows: picked cards hottest first (Do, or decided with a PR), then the PR no card owns.
const out = { outcomes, dispatches: [], ci }
const rows = workRows({ proposals, out, team })
assert.deepEqual(rows.map((r) => r.id), ['prop_bg_tasks', 'prop_one_step_undo', 'prop_plain_errors', 'example-org/example-repo#103'])
const view = (r) => Object.fromEntries(steps(r, ci).map((s) => [s.k, [s.done, s.text]]))
const bg = view(rows[0])
assert.deepEqual(Object.keys(bg), ['picked', 'chat', 'pr', 'ci', 'merged', 'judge'])
assert.deepEqual(Object.values(bg).map(([d]) => d), [true, true, true, true, true, true], 'a merged, scored card is done on every step')
assert.deepEqual(bg.ci, [true, 'pass'])
assert.deepEqual(view(rows[1]).ci, [false, 'running'])
assert.deepEqual(view(rows[1]).picked, [false, 'Later'])
assert.deepEqual(view(rows[2]).chat, [false, '—'])
assert.deepEqual(view(rows[3]).picked, [false, 'No card'])
assert.equal(steps(rows[3], ci).find((s) => s.k === 'ci').bad, true, 'a failing CI is marked')
assert.deepEqual(steps(rows[1], { checks: {} }).find((s) => s.k === 'ci').text, 'not read yet')
// Team flags: PR 101's item sits under a stale lane snapshot.
assert.equal(teamItems(team).length, 8)
assert.deepEqual(flags(rows[0]), ['stale'])
assert.deepEqual(flags(rows[2]), [])
// Joined by worker chat too: a blocked open item, a failed dispatch.
{
  const t2 = { leads: [{ key: 'L', stale: false, items: [{ item_id: 'i1', state: 'open', status: 'blocked', summary: 'needs a token', worker_session_key: 'chat-w1', pr: null, stale: false, orphaned: false }] }], loose_lanes: [] }
  const o2 = { outcomes: [], dispatches: [{ card_id: 'prop_plain_errors', state: 'dispatched', session: 'chat-w1' }], ci: { checks: {} } }
  const r = workRows({ proposals, out: o2, team: t2 }).find((x) => x.id === 'prop_plain_errors')
  assert.deepEqual(flags(r), ['blocked'])
  assert.equal(steps(r, o2.ci).find((s) => s.k === 'chat').href, '/chat?slot=chat-w1')
  assert.deepEqual(flags({ d: { state: 'error' }, items: [] }), ['failed'])
}

// The rendered tab in demo mode.
const src = demoSource()
const page = () => expand(h(HarnessRsi, { src, demo: true }))
const work = await tabTo(page, await __settle(page, true), 'work')
const rowsShown = byTest(work, 'work-row')
assert.equal(rowsShown.length, 4)
assert.ok(rowsShown.every((r) => byTest([r], 'work-step').length === 6))
assert.equal(rowsShown[0].props['data-flags'], 'stale')
assert.ok(byTest([rowsShown[0]], 'work-chat').every((a) => a.props.href === '/chat?slot=rsi-bg-tasks-1001093000'))
assert.equal(byTest(work, 'work-score').length, 1, 'Score PRs is on the Work tab')
assert.ok(byTest(work, 'src').some((n) => n.props['data-route'] === '/outcomes'))
assert.equal(byTest(work, 'work-ci-error').length, 0)

// Live: a failed CI read says why and keeps the rows; an empty backend is an empty Work tab with a next step.
{
  const live = { ...src, outcomes: async () => ({ outcomes, dispatches: [], score: null, ci: { checks: {}, read_at: null, error: 'ci: GitHub rate limit' } }) }
  const p2 = () => expand(h(HarnessRsi, { src: live }))
  const tree = await tabTo(p2, await __settle(p2, true), 'work')
  assert.equal(text(byTest(tree, 'work-ci-error')[0]), 'CI not read just now: ci: GitHub rate limit. Showing the last read.')
  assert.equal(byTest(tree, 'work-row').length, 4)
  const empty = { ...src, load: async () => ({ proposals: [], signals: [], images: {} }), outcomes: async () => ({ outcomes: [], dispatches: [], score: null }), team: async () => null }
  const p3 = () => expand(h(HarnessRsi, { src: empty }))
  const none = await tabTo(p3, await __settle(p3, true), 'work')
  assert.equal(byTest(none, 'work-row').length, 0)
  assert.equal(byTest(none, 'work-empty').length, 1)
}
console.log('work ok')
