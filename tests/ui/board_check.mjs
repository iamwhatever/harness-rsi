// Run by test_ui.py beside fake `react` / `@kirocrew/app-sdk/ui` (elements are plain objects).
import assert from 'node:assert/strict'
import fs from 'node:fs'
import * as ui from './ui/index.mjs'
import { loadFixtures } from './ui/fake-data.mjs'

const [proposals, signals] = ['proposals', 'signals'].map((f) => JSON.parse(fs.readFileSync(`fixtures/${f}.json`, 'utf8')))

// Expand function components into host elements, then flatten.
const expand = (n) => (Array.isArray(n) ? n.flatMap(expand) : n == null || typeof n !== 'object' ? [n]
  : typeof n.type === 'function' ? expand(n.type(n.props)) : [{ ...n, children: expand(n.props.children) }])
const all = (nodes) => nodes.filter((n) => n && typeof n === 'object').flatMap((n) => [n, ...all(n.children)])
const text = (n) => (n && typeof n === 'object' ? n.children.map(text).join('') : n == null ? '' : String(n))
const find = (nodes, pred) => all(nodes).filter(pred)

assert.deepEqual(await loadFixtures(), { proposals, signals, images: {} })

// The backend source: two reads, one decision post, refresh forwards the Slack export.
const sent = []
const api = (slackOk) => ({
  get: async (p) => { if (p.endsWith('/proposals')) return { proposals }; if (p.endsWith('/signals') && p.includes('harness-rsi')) return { signals }
    if (!slackOk) throw new Error('not permitted'); return { signals: signals.slice(0, 1) } },
  post: async (p, b) => { sent.push([p, b]); return p.endsWith('/refresh') ? { ok: true, total: 3, added: 1, errors: [] } : { ok: true } },
})
assert.deepEqual(await ui.backendSource(api(true)).load(), { proposals, signals, images: {} })
await ui.backendSource(api(true)).decide('prop_bg_tasks', 'do')
assert.deepEqual((await ui.backendSource(api(true)).refresh()).errors, [])
assert.deepEqual((await ui.backendSource(api(false)).refresh()).errors, ['slack: Slack Radar export not reachable'])
assert.deepEqual(sent, [['/api/apps/harness-rsi/decisions', { proposal_id: 'prop_bg_tasks', decision: 'do' }],
  ['/api/apps/harness-rsi/refresh', { slack: signals.slice(0, 1) }], ['/api/apps/harness-rsi/refresh', { slack: [] }]])

const calls = []
const tree = expand(ui.Board({ proposals, onDecide: (id, d) => calls.push([id, d]) }))
const cards = find(tree, (n) => n.props['data-testid'] === 'proposal-card')
assert.equal(cards.length, proposals.length)
for (const p of proposals) {
  const card = cards.find((c) => c.props['data-id'] === p.id)
  const t = text(card)
  const want = [p.pain, `${p.heat.people} people / ${p.heat.window_days} days`, `${p.cost.files} files · ${p.cost.lines} lines`,
    ...p.exam_ids, ...p.cost.risks, p.mock_artifact_slug ? `Open mock: ${p.mock_artifact_slug}` : 'No mock yet']
  for (const w of want) assert.ok(t.includes(w), `${p.id}: ${w}`)
  assert.equal(text(find([card], (n) => n.props.id === card.props['aria-labelledby'])[0]), p.pain)
  const buttons = find([card], (n) => n.type === 'button')
  assert.deepEqual(buttons.map(text), ['做', '不做', '以后再说'])
  for (const b of buttons) {
    assert.equal(b.props.type, 'button')
    assert.equal(b.props['aria-pressed'], p.decision === b.props['data-decision'])
    b.props.onClick()
  }
}
assert.deepEqual(calls, ui.byHeat(proposals).flatMap((p) => ['do', 'skip', 'later'].map((d) => [p.id, d])))

for (const d of ['do', 'skip', 'later']) {
  const next = ui.applyDecision(proposals, 'prop_bg_tasks', d)
  assert.deepEqual(next, proposals.map((p) => (p.id === 'prop_bg_tasks' ? { ...p, decision: d } : p)))
}

// Signals: one row each, primaries by people (desc), merged rows last.
const rows = find(expand(ui.Signals({ signals })), (n) => n.props['data-testid'] === 'signal-row')
const order = rows.map((r) => signals.find((s) => s.id === r.key))
assert.equal(order.length, signals.length)
const primary = order.filter((s) => !s.dedup_of)
assert.deepEqual(order.slice(0, primary.length), primary)
const people = primary.map((s) => s.mentions.people)
assert.deepEqual(people, [...people].sort((a, b) => b - a))
console.log('board ok')
