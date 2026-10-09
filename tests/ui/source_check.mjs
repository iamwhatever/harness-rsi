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
    errors: slackOn ? [] : ['slack: off (no Slack connector set)'] } : p.endsWith('/settings') ? { settings: shown(b) }
    : p.endsWith('/round/run') ? { ok: true, round: roundJob } : { ok: true } },
})
assert.deepEqual(await ui.backendSource(api(true)).load(), { proposals, signals, images: {} })
await ui.backendSource(api(true)).decide('prop_bg_tasks', 'do')
assert.deepEqual((await ui.backendSource(api(true)).refresh()).errors, [])
assert.deepEqual((await ui.backendSource(api(false)).refresh()).errors, ['slack: off (no Slack connector set)'])
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
assert.equal(text(find(panel, (n) => n.props['data-testid'] === 'slack-configured')[0]), 'Slack connector set: no')
const panelOn = expand(ui.SettingsForm({ form: ui.toForm(saved), note: '', onSave() {}, onChange() {} }))
assert.equal(text(find(panelOn, (n) => n.props['data-testid'] === 'slack-configured')[0]), 'Slack connector set: yes')
assert.ok(!JSON.stringify(ui.toForm(saved)).includes('slack-mcp'))
// The connector is a picker over the Slack MCP servers in mcp.json; typing a command is under Advanced.
assert.equal(find(panel, (n) => n.props['data-testid'] === 'slack-pick-none').length, 1, 'no connector found says where to add one')
const pick = find(panel, (n) => n.type === 'select')[0]
assert.equal(pick.props.name, 'pick')
assert.deepEqual(find([pick], (n) => n.type === 'option').map((n) => n.props.value), ['', ui.ADVANCED])
assert.deepEqual(find(panel, (n) => n.type === 'input').map((n) => n.props.name), ['channels', 'window_days', 'workspace_url'])
const adv = expand(ui.SettingsForm({ form: { ...form, pick: ui.ADVANCED }, note: '', onSave: () => changed.push('save'), onChange: (f) => changed.push(f) }))
const inputs = find(adv, (n) => n.type === 'input')
assert.deepEqual(inputs.map((n) => n.props.name), ['command', 'args', 'channels', 'window_days', 'workspace_url'])
inputs[0].props.onChange('slack-mcp')
find(adv, (n) => n.type === 'button')[0].props.onClick()
assert.deepEqual(changed, [{ ...form, pick: ui.ADVANCED, command: 'slack-mcp' }, 'save'])
const found = { ...conf, command_set: true, server: 'team-slack', repos: [],
  servers: [{ name: 'team-slack', source: 'workspace', usable: true }, { name: 'slack-shell', source: 'user', usable: false }] }
const picked = expand(ui.SettingsForm({ form: ui.toForm(found), note: '', onSave() {}, onChange() {} }))
assert.equal(text(find(picked, (n) => n.props['data-testid'] === 'slack-configured')[0]), 'Slack connector: team-slack')
assert.deepEqual(find(picked, (n) => n.type === 'option').map((n) => [n.props.value, text(n)]),
  [['', 'Keep the current one'], ['team-slack', 'team-slack (this workspace)'], [ui.ADVANCED, 'Advanced: type the command']])
assert.match(text(find(picked, (n) => n.props['data-testid'] === 'slack-pick-unusable')[0]), /slack-shell/)
assert.equal(find(picked, (n) => n.props['data-testid'] === 'slack-pick-none').length, 0)
// A picked name is sent as `pick` (the backend copies its command and args); a typed command only under Advanced.
assert.deepEqual(ui.parseSettings({ ...ui.toForm(found), pick: 'team-slack', command: 'ignored' }),
  { pick: 'team-slack', channels: conf.channels, window_days: 14, workspace_url: '', repos: [] })
assert.deepEqual(ui.parseSettings({ ...form, pick: ui.ADVANCED, command: 'x', args: '-a -b' }).args, ['-a', '-b'])
// GitHub: the repo list, each with its last read.
const ghConf = { ...conf, repos: ['o/one', 'o/two', 'o/three'], fetch: { 'o/one': { at: 1767603900, rows: 4, error: '' }, 'o/two': { at: 1767603900, rows: null, error: 'github: o/two: adapter exit 3' } } }
const ghForm = ui.toForm(ghConf)
assert.equal(ghForm.repos, 'o/one, o/two, o/three')
assert.deepEqual(ui.parseSettings({ ...ghForm, repos: 'a/b,  c/d' }).repos, ['a/b', 'c/d'])
const gh = expand(ui.GithubForm({ form: ghForm, note: '', onSave() {}, onChange() {} }))
const ghRows = find(gh, (n) => n.props['data-testid'] === 'gh-repo')
assert.deepEqual(ghRows.map((n) => n.props['data-repo']), ['o/one', 'o/two', 'o/three'])
assert.match(text(ghRows[0]), /^o\/one4 issue\(s\) at /)
assert.match(text(ghRows[1]), /failed at .*\(github: o\/two: adapter exit 3\)$/)
assert.equal(text(ghRows[2]), 'o/threenot read yet')
const ghOff = expand(ui.GithubForm({ form: ui.toForm({ ...conf, repos: [] }), note: '', onSave() {}, onChange() {} }))
assert.equal(text(find(ghOff, (n) => n.props['data-testid'] === 'gh-off')[0]), 'No repo set: GitHub is not read.')
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

// Schedule: the source reads and saves it; the form starts off and edits each field; Runs lists the runs.
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
assert.equal(text(expand(ui.Runs({ runs: [] }))[0]), 'No scheduled runs yetNext: run a round below, or turn on the weekly round in Settings.')
const runs = [{ kind: 'regress', start: '2026-10-05T09:05:00+00:00', end: '2026-10-05T09:40:00+00:00', sha: 'f00dfeed1234', regressions: 2, error: '' },
  { kind: 'round', start: '2026-10-05T09:05:00+00:00', end: '2026-10-05T10:05:00+00:00', signals: 12, cards: 3, error: '' },
  { kind: 'manual_round', start: '2026-10-06T09:05:00+00:00', end: '2026-10-06T10:05:00+00:00', signals: 4, cards: 3, error: '' }]
const runRows = find(expand(ui.Runs({ runs })), (x) => x.props['data-testid'] === 'run-row')
assert.deepEqual(runRows.map((r) => text(r.children[0])), ['Daily regression', 'Weekly round', 'Manual round'])
assert.deepEqual(runRows.map((r) => text(r.children[3])), ['2 regression(s) at f00dfee', '3 cards from 12 signals', '3 cards from 4 signals'])
assert.equal(ui.runResult({ kind: 'round', error: 'only 2 proposals' }), 'Failed: only 2 proposals')
// A decision shows on its card at once, before the reload.
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
// Outcomes: the source reads, links and scores; the judge line and regress line of a linked PR.
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
assert.equal(ui.scoreJobText(null), '')
assert.match(ui.scoreJobText({ running: true, started_at: 1 }), /^Scoring PRs since /)
assert.match(ui.scoreJobText({ running: false, started_at: 1, finished_at: 2, updated: [{}], error: '' }), /: 1 PR\(s\) changed$/)
// Prompt change: the source reads the changes and posts a decision.
const { promptChange: pc } = await import('./ui/fake-data.mjs')
const papi = { get: async (p) => { assert.equal(p, '/api/apps/harness-rsi/prompt-changes'); return { changes: [pc] } },
  post: async (p, b) => { assert.equal(p, '/api/apps/harness-rsi/prompt-changes/decide'); return { change: { ...pc, status: 'applied', b } } } }
assert.deepEqual(await ui.backendSource(papi).promptChanges(), [pc])
assert.deepEqual((await ui.backendSource(papi).decidePrompt(pc.id, 'do')).b, { id: pc.id, decision: 'do' })
// Auto-dispatch: the source reads and saves it; the form starts off.
const dconf = { auto_dispatch: false, repos: ['kirodotdev/KiroCrew'], daily_cap: 2, trust_dispatched: false }
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
const tbox = find(dform, (n) => n.props?.name === 'trust_dispatched')[0]
assert.equal(tbox.props.checked, false)  // trust starts off; its risk line is always shown beside it
tbox.props.onChange(true)
assert.ok(text(find(dform, (n) => n.props?.['data-testid'] === 'trust-risk')[0]).startsWith('Risk: a trusted chat runs tools without asking you'))
find(dform, (n) => n.type === 'button')[0].props.onClick()
assert.deepEqual(dedits, [{ ...dconf, auto_dispatch: true }, { ...dconf, repos: ['a/b', 'c/d'] }, { ...dconf, daily_cap: 3 }, { ...dconf, trust_dispatched: true }, 'save'])
// Manual dispatch: the source posts the ids; only a Do card with no live chat (or a failed one) may be dispatched.
const mposts = []
const mapi = { post: async (p, b) => { mposts.push([p, b]); return { results: [], dispatches: [] } } }
await ui.backendSource(mapi).dispatchCards(['prop_plain_errors'])
assert.deepEqual(mposts, [['/api/apps/harness-rsi/dispatch/start', { proposal_ids: ['prop_plain_errors'] }]])
assert.ok(ui.dispatchable({ decision: 'do' }, { state: 'error' }) && !ui.dispatchable({ decision: 'do' }, { state: 'pending' }) && !ui.dispatchable({ decision: 'later' }))
// Each result reads plainly; started and already link the worker chat.
const res = (r) => expand(ui.DispatchResult({ result: { id: 'x', ...r } }))[0]
assert.equal(text(res({ result: 'started', session: 'rsi-a' })), 'Started. Worker chat: rsi-a')
assert.equal(find([res({ result: 'started', session: 'rsi a' })], (n) => n.type === 'a')[0].props.href, '/chat?slot=rsi%20a')
assert.equal(text(res({ result: 'already', session: 'rsi-a' })), 'Already dispatched. Worker chat: rsi-a')
assert.equal(text(res({ result: 'already', session: null })), 'Already dispatched: opening a worker chat…')
assert.equal(text(res({ result: 'over_cap' })), 'Waiting for tomorrow: the daily cap is reached.')
assert.equal(text(res({ result: 'not_do' })), 'Not started: decide Do first.')
assert.equal(res({ result: 'error', reason: 'boom' }).props.role, 'alert')
assert.equal(text(res({ result: 'error', reason: 'boom' })), 'Not started: boom')
assert.equal(ui.DispatchResult({ result: undefined }), null)
console.log('source ok')
