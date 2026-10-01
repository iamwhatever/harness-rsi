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
const conf = { command_set: false, channels: ['C0AGA4Y4NP7'], window_days: 14, workspace_url: '' }
const shown = (b) => ({ command_set: !!b.command, channels: b.channels, window_days: b.window_days, workspace_url: b.workspace_url })
const roundJob = { running: true, round: 5, started_at: 1, finished_at: null, counts: null, notes: [], error: '' }
const api = (slackOn) => ({
  get: async (p) => { if (p.endsWith('/proposals')) return { proposals }; if (p.endsWith('/refresh/status')) return { github: { running: true } }
    if (p.endsWith('/round/status')) return { round: roundJob }
    if (p.endsWith('/settings')) return { settings: conf }; if (p === '/api/apps/harness-rsi/signals') return { signals }
    throw new Error(`unexpected read ${p}`) },
  post: async (p, b) => { sent.push([p, b]); return p.endsWith('/refresh') ? { ok: true, total: 3, added: 1,
    errors: slackOn ? [] : ['slack: off (no Slack MCP command set)'] } : p.endsWith('/settings') ? { settings: shown(b) }
    : p.endsWith('/round/run') ? { ok: true, round: roundJob } : { ok: true } },
})
assert.deepEqual(await ui.backendSource(api(true)).load(), { proposals, signals, images: {} })
await ui.backendSource(api(true)).decide('prop_bg_tasks', 'do')
assert.deepEqual((await ui.backendSource(api(true)).refresh()).errors, [])
assert.deepEqual((await ui.backendSource(api(false)).refresh()).errors, ['slack: off (no Slack MCP command set)'])
assert.deepEqual(await ui.backendSource(api(true)).settings(), conf)
const form = ui.toForm(conf)
assert.equal(form.command, '')
assert.deepEqual(ui.parseSettings(form), { channels: conf.channels, window_days: 14, workspace_url: '' })  // blank keeps the saved command
const saved = await ui.backendSource(api(true)).saveSettings({ ...form, command: 'slack-mcp', args: '--a  --b', channels: 'C0FAKE00001, C0FAKE00002' })
assert.deepEqual(saved, { ...conf, command_set: true, channels: ['C0FAKE00001', 'C0FAKE00002'] })
assert.match(ui.slackNote(conf), /^Slack collection is off/)
assert.equal(ui.slackNote(saved), 'Slack: on, reading 2 channel(s) over 14 days')
const changed = []
const panel = expand(ui.SettingsForm({ form, note: '', onSave: () => changed.push('save'), onChange: (f) => changed.push(f) }))
assert.match(text(find(panel, (n) => n.props['data-testid'] === 'slack-note')[0]), /^Slack collection is off/)
assert.equal(text(find(panel, (n) => n.props['data-testid'] === 'slack-configured')[0]), 'Slack MCP command configured: no')
const panelOn = expand(ui.SettingsForm({ form: ui.toForm(saved), note: '', onSave() {}, onChange() {} }))
assert.equal(text(find(panelOn, (n) => n.props['data-testid'] === 'slack-configured')[0]), 'Slack MCP command configured: yes')
assert.ok(!JSON.stringify(ui.toForm(saved)).includes('slack-mcp'))
const inputs = find(panel, (n) => n.type === 'input')
assert.deepEqual(inputs.map((n) => n.props.name), ['command', 'args', 'channels', 'window_days', 'workspace_url'])
inputs[0].props.onChange({ target: { value: 'slack-mcp' } })
find(panel, (n) => n.type === 'button')[0].props.onClick()
assert.deepEqual(changed, [{ ...form, command: 'slack-mcp' }, 'save'])
assert.deepEqual(await ui.backendSource(api(true)).status(), { running: true })
const job = { running: false, started_at: 1, finished_at: 2, rows: 5, error: '' }
assert.equal(ui.jobText(null), '')
assert.match(ui.jobText({ ...job, running: true }), /^GitHub: fetching since /)
assert.equal(ui.jobText({ ...job, finished_at: null }), 'GitHub: not fetched yet')
assert.match(ui.jobText(job), /^GitHub: 5 rows at /)
assert.match(ui.jobText({ ...job, error: 'github: TimeoutExpired' }), /failed at .*\(github: TimeoutExpired\)$/)
assert.deepEqual(await ui.backendSource(api(true)).round(), roundJob)
assert.deepEqual(await ui.backendSource(api(true)).runRound(), roundJob)
assert.deepEqual(sent, [['/api/apps/harness-rsi/decisions', { proposal_id: 'prop_bg_tasks', decision: 'do' }],
  ['/api/apps/harness-rsi/refresh', {}], ['/api/apps/harness-rsi/refresh', {}],
  ['/api/apps/harness-rsi/settings', { command: 'slack-mcp', args: ['--a', '--b'], channels: ['C0FAKE00001', 'C0FAKE00002'],
    window_days: 14, workspace_url: '' }], ['/api/apps/harness-rsi/round/run', {}]])

// Run round: the first click arms, only the second runs; a running round shows no live button.
const steps = []
const rr = (props) => find(expand(ui.RunRound({ onArm: () => steps.push('arm'), onConfirm: () => steps.push('run'),
  onCancel: () => steps.push('cancel'), ...props })), (n) => n.type === 'button')
const idle = rr({})
assert.deepEqual(idle.map(text), ['Run round'])
idle[0].props.onClick()
const armedBtns = rr({ armed: true })
assert.deepEqual(armedBtns.map(text), ['Confirm: run round', 'Cancel'])
assert.equal(armedBtns[0].props['aria-label'], undefined)
armedBtns[0].props.onClick()
armedBtns[1].props.onClick()
assert.deepEqual(steps, ['arm', 'run', 'cancel'])
const busy = rr({ running: true, armed: true })
assert.deepEqual(busy.map(text), ['Round running…'])
assert.equal(busy[0].props.disabled, true)
assert.equal(ui.roundText(null), '')
assert.match(ui.roundText(roundJob), /^Round 5: running since /)
assert.equal(ui.roundText({ ...roundJob, running: false }), 'Round: not run yet')
const ended = { ...roundJob, running: false, finished_at: 2, counts: { signals: 12, proposals: 4 }, notes: ['slack: off'] }
assert.match(ui.roundText(ended), /^Round 5: 4 proposals from 12 signals at .* · slack: off$/)
assert.match(ui.roundText({ ...ended, error: 'only 2 proposals' }), /failed at .*\(only 2 proposals\)$/)

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
