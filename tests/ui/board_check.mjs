// Run by test_ui.py beside the fakes in tests/ui/fakes (elements are plain objects).
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
inputs[0].props.onChange('slack-mcp')
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

// Schedule: the source reads and saves it; the form starts off and edits each field; the board lists runs.
const sched = { schedule: { round_enabled: false, regress_enabled: false, weekday: 0, hour: 9, kirocrew_dir: '' }, runs: [] }
const sapi = { get: async (p) => { assert.equal(p, '/api/apps/harness-rsi/schedule'); return sched },
  post: async (p, b) => { assert.equal(p, '/api/apps/harness-rsi/schedule'); return { ok: true, schedule: b } } }
assert.deepEqual(await ui.backendSource(sapi).schedule(), sched)
assert.deepEqual(await ui.backendSource(sapi).saveSchedule({ ...sched.schedule, round_enabled: true }), { ...sched.schedule, round_enabled: true })
const edits = []
const sform = expand(ui.ScheduleForm({ conf: sched.schedule, note: '', onSave: () => edits.push('save'), onChange: (c) => edits.push(c) }))
const named = (n) => find(sform, (x) => x.props.name === n)[0]
assert.deepEqual(find(sform, (x) => x.type === 'input' && x.props.type === 'checkbox').map((x) => [x.props.name, x.props.checked]),
  [['round_enabled', false], ['regress_enabled', false], ['score_enabled', false]])
assert.deepEqual(find([named('weekday')], (x) => x.type === 'option').map(text), ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'])
assert.equal(named('weekday').props.value, '0')
named('round_enabled').props.onChange(true)
named('weekday').props.onChange('3')
named('hour').props.onChange('7')
named('kirocrew_dir').props.onChange('/kc')
find(sform, (x) => x.type === 'button')[0].props.onClick()
assert.deepEqual(edits, [{ ...sched.schedule, round_enabled: true }, { ...sched.schedule, weekday: 3 }, { ...sched.schedule, hour: 7 },
  { ...sched.schedule, kirocrew_dir: '/kc' }, 'save'])
assert.match(text(sform[0]), /nothing posts to Slack, opens a PR or merges/)
assert.equal(text(expand(ui.Runs({ runs: [] }))[0]), 'No scheduled runs yet')
const runs = [{ kind: 'regress', start: '2026-10-05T09:05:00+00:00', end: '2026-10-05T09:40:00+00:00', sha: 'f00dfeed1234', regressions: 2, error: '' },
  { kind: 'round', start: '2026-10-05T09:05:00+00:00', end: '2026-10-05T10:05:00+00:00', signals: 12, cards: 3, error: '' }]
const runRows = find(expand(ui.Runs({ runs })), (x) => x.props['data-testid'] === 'run-row')
assert.deepEqual(runRows.map((r) => text(r.children[0])), ['Daily regression', 'Weekly round'])
assert.deepEqual(runRows.map((r) => text(r.children[3])), ['2 regression(s) at f00dfee', '3 cards from 12 signals'])
assert.equal(ui.runResult({ kind: 'round', error: 'only 2 proposals' }), 'Failed: only 2 proposals')

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
  assert.deepEqual(buttons.map(text), ['Do', 'Skip', 'Later'])
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
// Outcomes: the source reads, links and scores; a card shows each linked PR's judge line and regress line.
const outcomes = JSON.parse(fs.readFileSync('fixtures/outcomes.json', 'utf8'))
const osent = []
const oapi = { get: async (p) => { assert.equal(p, '/api/apps/harness-rsi/outcomes'); return { outcomes, score: null } },
  post: async (p, b) => { osent.push([p, b]); return { ok: true } } }
assert.deepEqual(await ui.backendSource(oapi).outcomes(), { outcomes, score: null })
await ui.backendSource(oapi).link('prop_bg_tasks', 15792)
await ui.backendSource(oapi).score()
assert.deepEqual(osent, [['/api/apps/harness-rsi/outcomes/link', { proposal_id: 'prop_bg_tasks', pr: 15792 }],
  ['/api/apps/harness-rsi/score/run', {}]])
assert.equal(ui.prUrl('kirodotdev/KiroCrew#15792'), 'https://github.com/kirodotdev/KiroCrew/pull/15792')
assert.equal(ui.scoreText(outcomes[0]), 'Judge: base fail 0/1 → head pass 1/1')
assert.equal(ui.scoreText(outcomes[1]), 'Not scored yet')
assert.equal(ui.scoreText(outcomes[2]), 'Not scored: no card; no exam')
assert.equal(ui.regressLine(outcomes[0]), 'Regress @ 3333333: 1/1 pass')
assert.equal(ui.regressLine({ ...outcomes[0], regress: [{ ...outcomes[0].regress[0], exams: { a: 'fail' }, regressions: 1 }] }),
  'Regress @ 3333333: 0/1 pass · 1 regression(s)')
assert.equal(ui.regressLine(outcomes[1]), '')
const linked = []
const scored = expand(ui.Board({ proposals, outcomes, onDecide() {}, onLink: (id, n) => linked.push([id, n]) }))
const bg = find(scored, (n) => n.props['data-id'] === 'prop_bg_tasks')[0]
assert.equal(text(find([bg], (n) => n.props['data-testid'] === 'score')[0]), 'Judge: base fail 0/1 → head pass 1/1')
assert.equal(find([bg], (n) => n.type === 'a' && n.props.href.includes('/pull/'))[0].props.href, 'https://github.com/example-org/example-repo/pull/101')
const linkForm = find([bg], (n) => n.type === 'form')[0]
linkForm.props.onSubmit({ preventDefault() {}, target: { elements: { pr: { value: '15792' } } } })
linkForm.props.onSubmit({ preventDefault() {}, target: { elements: { pr: { value: '' } } } })  // blank: nothing linked
assert.deepEqual(linked, [['prop_bg_tasks', 15792]])
assert.ok(text(find(scored, (n) => n.props['data-id'] === 'prop_plain_errors')[0]).includes('No PR linked'))
assert.equal(ui.scoreJobText(null), '')
assert.match(ui.scoreJobText({ running: true, started_at: 1 }), /^Scoring PRs since /)
assert.match(ui.scoreJobText({ running: false, started_at: 1, finished_at: 2, updated: [{}], error: '' }), /: 1 PR\(s\) changed$/)
// Prompt change: the card shows A vs B per metric and the diff; Do sends the decision; an applied card has no buttons.
const { promptChange: pc } = await import('./ui/fake-data.mjs')
const papi = { get: async (p) => { assert.equal(p, '/api/apps/harness-rsi/prompt-changes'); return { changes: [pc] } },
  post: async (p, b) => { assert.equal(p, '/api/apps/harness-rsi/prompt-changes/decide'); return { change: { ...pc, status: 'applied', b } } } }
assert.deepEqual(await ui.backendSource(papi).promptChanges(), [pc])
assert.deepEqual((await ui.backendSource(papi).decidePrompt(pc.id, 'do')).b, { id: pc.id, decision: 'do' })
const picked = []
const pcard = expand(ui.PromptChangeCard({ change: pc, onDecide: (id, d) => picked.push([id, d]) }))
const ptext = text(pcard[0])
for (const w of ['Prompt change', pc.summary, 'vaaaaaaaaaa → v0123456789', 'setter_hit_rate', '0.1', '0.3', 'better', '+7. Another new rule.']) {
  assert.ok(ptext.includes(w), w)
}
find(pcard, (n) => n.type === 'button').forEach((b) => b.props.onClick())
assert.deepEqual(picked, [[pc.id, 'do'], [pc.id, 'skip'], [pc.id, 'later']])
const done = expand(ui.PromptChangeCard({ change: { ...pc, status: 'applied', ab: null }, onDecide() {} }))
assert.equal(find(done, (n) => n.type === 'button').length, 0)
assert.ok(text(done[0]).includes('Applied: rsi-question-setter now runs v0123456789') && text(done[0]).includes('No A/B run yet'))
// Auto-dispatch: the source reads and saves it; the form starts off; a card shows its worker chat or why it failed.
const dconf = { auto_dispatch: false, repos: ['kirodotdev/KiroCrew'], daily_cap: 2 }
const dapi = { get: async (p) => { assert.equal(p, '/api/apps/harness-rsi/dispatch'); return { dispatch: dconf } },
  post: async (p, b) => { assert.equal(p, '/api/apps/harness-rsi/dispatch'); return { dispatch: b } } }
assert.deepEqual(await ui.backendSource(dapi).dispatchConf(), dconf)
assert.deepEqual(await ui.backendSource(dapi).saveDispatch({ ...dconf, auto_dispatch: true }), { ...dconf, auto_dispatch: true })
const dedits = []
const dform = expand(ui.DispatchForm({ conf: dconf, note: '', onSave: () => dedits.push('save'), onChange: (c) => dedits.push(c) }))
const dbox = find(dform, (n) => n.props?.name === 'auto_dispatch')[0]
assert.equal(dbox.props.checked, false)
dbox.props.onChange(true)
find(dform, (n) => n.props?.name === 'repos')[0].props.onChange('a/b, c/d')
find(dform, (n) => n.props?.name === 'daily_cap')[0].props.onChange('3')
find(dform, (n) => n.type === 'button')[0].props.onClick()
assert.deepEqual(dedits, [{ ...dconf, auto_dispatch: true }, { ...dconf, repos: ['a/b', 'c/d'] }, { ...dconf, daily_cap: 3 }, 'save'])
const sentRow = { card_id: 'prop_bg_tasks', state: 'dispatched', session: 'rsi-bg-tasks-1', error: '' }
const dcard = expand(ui.ProposalCard({ proposal: proposals[0], onDecide() {}, dispatch: sentRow }))
assert.equal(find(dcard, (n) => n.type === 'a' && n.props.href === '/chat?slot=rsi-bg-tasks-1').length, 1)
const failedLine = expand(ui.DispatchLine({ row: { ...sentRow, state: 'error', session: null, error: 'daily cap of 2 reached' } }))
assert.equal(failedLine[0].props.role, 'alert')
assert.equal(text(failedLine[0]), 'Dispatch failed: daily cap of 2 reached')
assert.equal(ui.DispatchLine({ row: undefined }), null)
console.log('board ok')
