// Harness RSI priority board. Hand-written ESM, no build step: the host's import
// map resolves `react`, `lucide-react` and `@kirocrew/app-sdk(/ui)`, as it does for a built bundle.
// Host components only, laid out like Dev Fleet / Issue Radar; every word on screen comes from strings.mjs.
import { createElement as h, useCallback, useEffect, useMemo, useState } from 'react'
import * as sdk from '@kirocrew/app-sdk'
import * as UI from '@kirocrew/app-sdk/ui'
// The host's lucide-react stub names only a short list of icons; every other icon is on its default export.
import Lucide from 'lucide-react'
import { getLang, has, pickLang, setLang, t } from './strings.mjs'
import { demoSource, isDemo } from './fake-data.mjs'

const { History, Inbox, Radio, Users, Wand2 } = Lucide
const BASE = '/api/apps/harness-rsi'

/** The data source over the app backend. `load` answers { proposals, signals, images }
 *  (images[id].before is a screenshot URL of the page today); demo mode uses fake-data.mjs instead. */
export function backendSource(api) {
  return {
    load: async () => {
      const [p, s] = await Promise.all([api.get(`${BASE}/proposals`), api.get(`${BASE}/signals`)])
      return { proposals: p.proposals, signals: s.signals, images: {} }
    },
    decide: (id, decision) => api.post(`${BASE}/decisions`, { proposal_id: id, decision }),
    status: async () => (await api.get(`${BASE}/refresh/status`)).github,
    round: async () => (await api.get(`${BASE}/round/status`)).round,
    runRound: async () => (await api.post(`${BASE}/round/run`, {})).round,
    regress: async () => (await api.get(`${BASE}/regress`)).runs[0] || null,
    // The backend reads Slack itself; when Slack is off or fails, errors say why.
    refresh: async () => { const r = await api.post(`${BASE}/refresh`, {}); return { ...r, errors: r.errors || [] } },
    settings: async () => (await api.get(`${BASE}/settings`)).settings,
    saveSettings: async (form) => (await api.post(`${BASE}/settings`, parseSettings(form))).settings,
    schedule: () => api.get(`${BASE}/schedule`),
    saveSchedule: async (conf) => (await api.post(`${BASE}/schedule`, conf)).schedule,
    outcomes: () => api.get(`${BASE}/outcomes`),
    link: (id, pr) => api.post(`${BASE}/outcomes/link`, { proposal_id: id, pr }),
    score: () => api.post(`${BASE}/score/run`, {}),
    promptChanges: async () => (await api.get(`${BASE}/prompt-changes`)).changes,
    decidePrompt: async (id, decision) => (await api.post(`${BASE}/prompt-changes/decide`, { id, decision })).change,
    dispatchConf: async () => (await api.get(`${BASE}/dispatch`)).dispatch,
    saveDispatch: async (conf) => (await api.post(`${BASE}/dispatch`, conf)).dispatch,
    team: async () => (await api.get(`${BASE}/team`)).team,
  }
}

const words = (x) => String(x || '').split(/[\s,]+/).filter(Boolean)
/** Form strings -> the POST /settings body; a blank command keeps the saved one (and its args). */
export const parseSettings = (f) => {
  const command = String(f.command || '').trim()
  return { ...(command ? { command, args: words(f.args) } : {}), channels: words(f.channels),
    window_days: Number(f.window_days), workspace_url: String(f.workspace_url || '').trim() }
}
/** The backend shows only whether a command is set, so its inputs start blank. */
export const toForm = (s) => ({ saved: s, command: '', args: '', channels: s.channels.join(', '),
  window_days: String(s.window_days), workspace_url: s.workspace_url })
export const slackNote = (s) => (s?.command_set ? t('slackOn', { n: s.channels.length, days: s.window_days }) : t('slackOff'))

const LABEL = 'text-[11px] uppercase tracking-wide text-muted font-semibold mb-1'
const MUTED = 'text-[13px] text-muted'
const Section = ({ title, children }) => h('div', { className: 'min-w-0' }, h('div', { className: LABEL }, title), children)
const Saver = ({ onSave, note, label }) => h('div', { className: 'flex flex-wrap items-center gap-3 mt-2' },
  h(UI.Btn, { type: 'button', primary: true, onClick: onSave }, label || t('save')), h('span', { className: MUTED, 'aria-live': 'polite' }, note))
const Table = ({ caption, cols, children, testId }) => h('div', { className: 'overflow-x-auto' },
  h('table', { className: 'w-full border-collapse text-[13px]', 'data-testid': testId },
    caption ? h('caption', { className: `${LABEL} text-left` }, caption) : null,
    h('thead', null, h('tr', null, cols.map((c) => h('th', { key: c, scope: 'col', className: 'text-left text-muted font-semibold px-2 py-2 border-b border-border whitespace-nowrap' }, c)))),
    h('tbody', null, children)))
const TD = 'px-2 py-2 border-b border-border'
const td = (...c) => h('td', { className: TD }, ...c)

const FIELDS = ['command', 'args', 'channels', 'window_days', 'workspace_url']
/** The Slack settings form: stateless, so the page owns the values. */
export function SettingsForm({ form, onChange, onSave, note }) {
  return h(UI.SettingsSection, { title: t('slackTitle') },
    h('div', { className: MUTED, 'data-testid': 'slack-configured' }, t(form.saved.command_set ? 'slackSetYes' : 'slackSetNo')),
    h('div', { className: MUTED, 'data-testid': 'slack-note' }, slackNote(form.saved)),
    FIELDS.map((k) => h(UI.SettingsInput, { key: k, name: k, label: t(`field_${k}`), value: form[k] ?? '', onChange: (v) => onChange({ ...form, [k]: v }) })),
    h(Saver, { onSave, note }))
}

/** The owner's schedule (backend.schedule): both jobs off until switched on here and saved. */
export function ScheduleForm({ conf, onChange, onSave, note }) {
  const tog = (k) => h(UI.SettingsToggle, { key: k, name: k, label: t(`sched_${k}`), checked: !!conf[k], onChange: (v) => onChange({ ...conf, [k]: v }) })
  return h(UI.SettingsSection, { title: t('schedTitle') },
    h('div', { className: MUTED }, t('schedIntro')),
    tog('round_enabled'),
    h(UI.SettingsSelect, { name: 'weekday', label: t('schedDay'), value: String(conf.weekday), options: ['0', '1', '2', '3', '4', '5', '6'],
      optionLabels: t('days').split(','), onChange: (v) => onChange({ ...conf, weekday: Number(v) }) }),
    h(UI.SettingsInput, { name: 'hour', type: 'number', min: 0, max: 23, label: t('schedHour'), value: String(conf.hour), onChange: (v) => onChange({ ...conf, hour: Number(v) }) }),
    tog('regress_enabled'), tog('score_enabled'),
    h(UI.SettingsInput, { name: 'kirocrew_dir', label: t('schedDir'), value: conf.kirocrew_dir, onChange: (v) => onChange({ ...conf, kirocrew_dir: v }) }),
    h(Saver, { onSave, note }))
}

/** The owner's auto-dispatch (backend.dispatch): off until switched on here and saved. */
export function DispatchForm({ conf, onChange, onSave, note }) {
  return h(UI.SettingsSection, { title: t('dispTitle') },
    h('div', { className: MUTED }, t('dispIntro')),
    h(UI.SettingsToggle, { name: 'auto_dispatch', label: t('dispToggle'), checked: !!conf.auto_dispatch, onChange: (v) => onChange({ ...conf, auto_dispatch: v }) }),
    h(UI.SettingsInput, { name: 'repos', label: t('dispRepos'), value: conf.repos.join(', '), onChange: (v) => onChange({ ...conf, repos: words(v) }) }),
    h(UI.SettingsInput, { name: 'daily_cap', type: 'number', min: 1, max: 10, label: t('dispCap'), value: String(conf.daily_cap), onChange: (v) => onChange({ ...conf, daily_cap: Number(v) }) }),
    h(Saver, { onSave, note }))
}

/** A card's worker-chat line: opening, a link to the chat, or why the dispatch failed. */
export function DispatchLine({ row: d }) {
  if (!d) return null
  if (d.state === 'error') return h('div', { role: 'alert', 'data-testid': 'dispatch-line', className: 'text-[13px] text-danger mt-2' }, t('dispFailed', { e: d.error }))
  return h('div', { 'data-testid': 'dispatch-line', className: `${MUTED} mt-2` }, d.state === 'pending' ? t('dispOpening')
    : h('a', { href: `/chat?slot=${encodeURIComponent(d.session)}`, className: 'text-accent hover:underline' }, t('dispChat', { s: d.session })))
}

const at = (s) => (s ? new Date(s).toLocaleString(getLang(), { dateStyle: 'short', timeStyle: 'short', hourCycle: 'h23' }) : '')
/** One line of result for a run record. */
export const runResult = (r) => (r.error ? t('runFailed', { e: r.error }) : r.kind === 'round' ? t('runCards', { cards: r.cards, signals: r.signals })
  : t(r.regressions ? 'runRegress' : 'runClean', { n: r.regressions, sha: String(r.sha || '').slice(0, 7) }))
/** The last scheduled runs, newest first. */
export function Runs({ runs }) {
  if (!runs.length) return h(UI.EmptyState, { icon: h(History, { size: 28 }), title: t('noRuns'), testId: 'no-runs' })
  return h(Table, { caption: t('runsTitle'), cols: [t('colJob'), t('colStarted'), t('colEnded'), t('colResult')], testId: 'schedule-runs' },
    runs.map((r) => h('tr', { key: r.kind + r.start, 'data-testid': 'run-row' },
      [t(r.kind === 'round' ? 'jobRound' : 'jobRegress'), at(r.start), at(r.end), runResult(r)].map((c, i) => h('td', { key: i, className: TD }, c)))))
}

export const DECISIONS = ['do', 'skip', 'later']
/** Most people first, then the shortest window. */
export const byHeat = (list) =>
  [...list].sort((a, b) => b.heat.people - a.heat.people || a.heat.window_days - b.heat.window_days)
/** Hottest first; a merged duplicate sinks below every primary signal. */
export const signalsByHeat = (list) =>
  [...list].sort((a, b) => !!a.dedup_of - !!b.dedup_of
    || b.mentions.people - a.mentions.people || b.mentions.count - a.mentions.count)
export const applyDecision = (list, id, decision) => list.map((p) => (p.id === id ? { ...p, decision } : p))

export const prUrl = (pr) => `https://github.com/${pr.replace('#', '/pull/')}`
/** A data word (state, layer, verdict) in the page's language, or as it is when the table does not know it. */
const word = (prefix, v) => (has(`${prefix}_${v}`) ? t(`${prefix}_${v}`) : v)
const runText = (r) => `${word('judge', r.verdict)} ${r.pass}/${r.pass + r.fail + r.error}`
/** The judge's line for one linked PR: base -> head, or why there is no score. */
export const scoreText = (o) => (o.score.head ? t('scoreJudge', { base: runText(o.score.base), head: runText(o.score.head) })
  : o.note ? t('scoreNot', { note: o.note }) : t('scoreYet'))
/** The newest post-merge regress result for the card's exams, or ''. */
export const regressLine = (o) => {
  const g = o.regress.at(-1)
  if (!g) return o.state === 'merged' && o.exam_ids.length ? t('regressNotRun') : ''
  const got = Object.values(g.exams)
  return t(g.regressions ? 'regressLineBad' : 'regressLine', { sha: g.sha.slice(0, 7), pass: got.filter((x) => x === 'pass').length, n: got.length, bad: g.regressions })
}
/** A card's product PRs with their scores; with `onLink` a PR number can be linked. */
export function Outcomes({ proposal: p, outcomes, onLink }) {
  const submit = (e) => { e.preventDefault(); const n = Number(e.target.elements.pr.value); if (n > 0) onLink(p.id, n) }
  return h(Section, { title: t('secPr') }, outcomes.map((o) => h('div', { key: o.pr, 'data-testid': 'outcome', className: 'flex flex-wrap items-center gap-2 mt-1' },
    h('a', { href: prUrl(o.pr), target: '_blank', rel: 'noreferrer', className: 'text-accent hover:underline' }, o.pr.split('/').pop()),
    h(UI.Badge, { variant: o.state === 'merged' ? 'ok' : 'muted' }, word('state', o.state)),
    h('span', { 'data-testid': 'score' }, scoreText(o)), h('span', { className: MUTED }, regressLine(o)))),
  !outcomes.length ? h('div', { className: MUTED }, t('noPr')) : null,
  onLink ? h('form', { onSubmit: submit, className: 'flex flex-wrap items-center gap-2 mt-2' },
    h(UI.Input, { name: 'pr', type: 'number', min: 1, placeholder: t('prPlaceholder'), 'aria-label': t('prAria', { pain: p.pain }), className: 'w-32' }),
    h(UI.Btn, { type: 'submit' }, t('linkPr'))) : null)
}

const PRIOR = 'prior_art: '
const Frame = ({ title, children }) => h('figure', { className: 'min-w-0 m-0' }, h('figcaption', { className: LABEL }, title),
  h('div', { className: 'h-36 rounded-md border border-border bg-bg-elevated flex items-center justify-center overflow-hidden' }, children))
const Decide = ({ value, label, onDecide }) => h('div', { role: 'group', 'aria-label': label, className: 'flex flex-wrap items-center gap-2 mt-3' },
  DECISIONS.map((d) => h(UI.Btn, { key: d, type: 'button', 'data-decision': d, primary: value === d, 'aria-pressed': value === d, onClick: () => onDecide(d) }, t(`dec_${d}`))),
  h('span', { className: MUTED, 'aria-live': 'polite' }, value ? t('decided', { d: t(`dec_${value}`) }) : t('undecided')))

/** One proposal: title, heat, sources, before/after, cost, prior art, exams, score, decision. */
export function ProposalCard({ proposal: p, before, onDecide, outcomes, onLink, dispatch, signals = [] }) {
  const titleId = `pain-${p.id}`
  const slug = p.mock_artifact_slug
  const srcs = signals.filter((s) => p.signal_ids.includes(s.id))
  const prior = p.cost.risks.filter((r) => r.startsWith(PRIOR)).map((r) => r.slice(PRIOR.length))
  const risks = p.cost.risks.filter((r) => !r.startsWith(PRIOR))
  return h(UI.Card, { role: 'region', 'aria-labelledby': titleId, 'data-testid': 'proposal-card', 'data-id': p.id },
    h('div', { className: 'flex flex-wrap items-center justify-between gap-2' },
      h('h3', { id: titleId, className: 'm-0 text-[15px] font-semibold text-text-strong min-w-0' }, p.pain),
      h(UI.Badge, { variant: 'warn', 'data-testid': 'heat' }, t('heat', { people: p.heat.people, days: p.heat.window_days }))),
    h('div', { className: 'flex flex-wrap items-center gap-2 mt-2', 'data-testid': 'sources' },
      srcs.length ? srcs.map((s) => h('a', { key: s.id, href: s.links[0], target: '_blank', rel: 'noreferrer', className: 'text-accent hover:underline text-[12px] font-mono' }, s.source))
        : h('span', { className: MUTED }, t('noSources'))),
    h('div', { className: 'grid gap-3 sm:grid-cols-2 mt-3' },
      h(Frame, { title: t('before') }, before ? h('img', { src: before, alt: t('beforeAlt', { pain: p.pain }), className: 'w-full h-full object-cover' })
        : h('span', { className: MUTED }, t('noShot'))),
      h(Frame, { title: t('after') }, slug ? h('a', { href: `/artifacts/${slug}`, className: 'text-accent hover:underline text-[13px]' }, t('openMock', { slug }))
        : h('span', { className: MUTED }, t('noMock')))),
    h('div', { className: 'grid gap-3 sm:grid-cols-2 mt-3' },
      h(Section, { title: t('secCost') }, h('div', { 'data-testid': 'cost' }, t('cost', { files: p.cost.files, lines: p.cost.lines })),
        h('div', { className: MUTED }, risks.length ? t('risks', { r: risks.join(', ') }) : t('noRisks'))),
      h(Section, { title: t('secPrior') }, h('div', { className: MUTED, 'data-testid': 'prior-art' }, prior.length ? prior.join(' · ') : t('noPrior'))),
      h(Section, { title: t('secExams') }, h('div', { className: 'flex flex-wrap gap-1.5' }, p.exam_ids.map((e) => h(UI.Badge, { key: e, variant: 'muted' }, e)))),
      outcomes || onLink ? h(Outcomes, { proposal: p, outcomes: outcomes || [], onLink }) : null),
    h(Decide, { value: p.decision, label: t('decAria', { pain: p.pain }), onDecide: (d) => onDecide(p.id, d) }),
    h(DispatchLine, { row: dispatch }))
}

const VERDICT = { better: 'ok', worse: 'err', same: 'muted' }
/** One proposed crew-prompt change: its A/B numbers and diff. Do applies it (a new prompt version). */
export function PromptChangeCard({ change: c, onDecide }) {
  const titleId = `pc-${c.id}`
  const num = (x) => (x == null ? '—' : String(x))
  return h(UI.Card, { role: 'region', 'aria-labelledby': titleId, 'data-testid': 'prompt-change-card', 'data-id': c.id },
    h('div', { className: LABEL }, t('pcLabel')),
    h('h3', { id: titleId, className: 'm-0 text-[15px] font-semibold text-text-strong' }, `${c.agent}: ${c.summary}`),
    h('div', { className: `${MUTED} mt-1` }, `${c.from} → ${c.to}`),
    c.ab ? h('div', { className: 'mt-3' }, h(Table, { testId: 'prompt-ab', caption: t('abCaption', { rounds: c.ab.rounds.join(', '), reps: c.ab.reps }),
      cols: [t('colMetric'), t('colA'), t('colB'), t('colVerdict')] }, Object.entries(c.ab.metrics).map(([m, v]) => h('tr', { key: m },
      td(h('code', null, m)), td(num(v.A)), td(num(v.B)), td(h(UI.Badge, { variant: VERDICT[v.verdict] || 'muted' }, word('verdict', v.verdict)))))))
      : h('div', { className: `${MUTED} mt-3` }, t('noAb')),
    h('details', { className: 'mt-3' }, h('summary', { className: MUTED }, t('showDiff')),
      h('pre', { className: 'text-[12px] whitespace-pre-wrap overflow-x-auto' }, c.diff)),
    c.status === 'applied' ? h('div', { className: `${MUTED} mt-3` }, t('pcApplied', { agent: c.agent, to: c.to }))
      : h(Decide, { value: c.status === 'pending' ? null : c.status, label: t('pcAria', { id: c.id }), onDecide: (d) => onDecide(c.id, d) }))
}

const empty = (icon, key) => h(UI.EmptyState, { icon: h(icon, { size: 28 }), title: t(key), subtitle: t('emptyHint') })
const stack = (...c) => h('div', { className: 'flex flex-col gap-3' }, ...c)
export function Board({ proposals, images = {}, onDecide, outcomes, onLink, dispatches = [], signals = [] }) {
  if (!proposals.length) return empty(Inbox, 'noProposals')
  return stack(byHeat(proposals).map((p) => h(ProposalCard, { key: p.id, proposal: p, before: images[p.id]?.before, onDecide, signals,
    outcomes: outcomes && outcomes.filter((o) => o.card_id === p.id), onLink, dispatch: dispatches.find((d) => d.card_id === p.id) })))
}

export function Signals({ signals }) {
  if (!signals.length) return empty(Radio, 'noSignals')
  const cols = ['colPain', 'colPeople', 'colMentions', 'colDays', 'colSource', 'colLayer'].map((k) => t(k))
  return h(UI.Card, null, h(Table, { caption: t('signalsCaption'), cols }, signalsByHeat(signals).map((s) => h('tr', { key: s.id, 'data-testid': 'signal-row' },
    td(h('a', { href: s.links[0], target: '_blank', rel: 'noreferrer', className: 'text-text hover:underline' }, s.pain),
      s.dedup_of ? h('div', { className: MUTED }, t('mergedInto', { id: s.dedup_of })) : null),
    td(s.mentions.people), td(s.mentions.count), td(s.mentions.window_days),
    td(h('code', null, s.source)), td(h(UI.Badge, { variant: s.layer === 'real' ? 'ok' : 'aim' }, word('layer', s.layer)))))))
}

/** The host chat for one session key. */
export const sessionHref = (key) => `/chat?slot=${encodeURIComponent(key)}`
const SessionLink = ({ k, label }) => h('a', { href: sessionHref(k), className: 'text-accent hover:underline', 'data-testid': 'session-link' }, label || k)
const STATUS_TONE = { progress: 'aim', done: 'ok', blocked: 'err', question: 'warn', accepted: 'ok', rejected: 'err', abandoned: 'muted', none: 'muted' }
/** An item's one status word: its worker status while open, else its closed state. */
export const itemBucket = (i) => (i.state === 'open' ? i.status || 'none' : i.state)
const bucketWord = (b) => (has(`status_${b}`) ? t(`status_${b}`) : word('istate', b))

/** One work item: status, title, verdict, PR, flags, and its worker session (or the lane it opened). */
function TeamItem({ item: i }) {
  const b = itemBucket(i)
  return h('li', { 'data-testid': 'team-item', 'data-id': i.item_id, className: 'py-1.5' },
    h('div', { className: 'flex flex-wrap items-center gap-2' },
      h(UI.Badge, { variant: STATUS_TONE[b] || 'muted' }, bucketWord(b)),
      h('span', { className: 'text-text-strong' }, i.title),
      i.verdict ? h(UI.Badge, { variant: i.verdict === 'pass' ? 'ok' : 'muted' }, word('wverdict', i.verdict)) : null,
      i.pr ? h('span', { className: MUTED }, t('prNum', { n: i.pr })) : null,
      i.orphaned ? h(UI.Badge, { variant: 'warn' }, t('orphaned')) : null,
      i.stale ? h(UI.Badge, { variant: 'warn' }, t('staleItem')) : null,
      !i.lane && i.worker_session_key ? h('span', { className: MUTED }, h(SessionLink, { k: i.worker_session_key })) : null),
    i.summary ? h('div', { className: MUTED }, i.summary) : null,
    i.lane ? h(Reporter, { rec: i.lane, nested: true }) : null)
}

/** One reporter (the lead or a lane): who, when it last pushed, and its items. */
function Reporter({ rec: r, nested, staleMinutes = 90 }) {
  return h('div', { 'data-testid': `team-${r.role}`, 'data-key': r.key, className: nested ? 'ml-4 mt-1 pl-3 border-l border-border' : '' },
    h('div', { className: 'flex flex-wrap items-center gap-2' },
      h(UI.Badge, { variant: r.role === 'lead' ? 'aim' : 'muted' }, t(r.role === 'lead' ? 'roleLead' : 'roleLane')),
      h(SessionLink, { k: r.key }),
      h('span', { className: MUTED }, r.round == null ? t('pushedNoRound', { at: at(r.received_at) }) : t('pushedAt', { round: r.round, at: at(r.received_at) })),
      r.stale ? h(UI.Badge, { variant: 'warn', 'data-testid': 'stale-reporter' }, t('staleReporter', { m: staleMinutes })) : null),
    r.goal ? h('div', { className: MUTED }, r.goal) : null,
    h('ul', { className: 'list-none m-0 p-0' }, r.items.map((i) => h(TeamItem, { key: i.item_id, item: i }))))
}

export const COUNT_ORDER = ['question', 'blocked', 'progress', 'done', 'none', 'accepted', 'rejected', 'abandoned']
/** The Team tab: self-reported note, counts by status, what needs you, then lead -> lanes -> workers. */
export function Team({ team }) {
  const note = h('div', { role: 'note', 'data-testid': 'team-note', className: MUTED }, t('teamNote'))
  if (!team || !(team.leads.length || team.loose_lanes.length)) {
    return stack(note, h(UI.EmptyState, { icon: h(Users, { size: 28 }), title: t('noTeam'), subtitle: t('teamHint') }))
  }
  const m = team.stale_minutes
  return stack(note,
    h('div', { className: 'flex flex-wrap gap-2', 'data-testid': 'team-counts' }, COUNT_ORDER.filter((k) => team.counts[k])
      .map((k) => h(UI.Badge, { key: k, variant: STATUS_TONE[k] }, t(`count_${k}`, { n: team.counts[k] })))),
    h(UI.Card, { key: 'needs' }, h('div', { className: LABEL }, t('needsTitle')),
      team.needs_you.length ? h('ul', { className: 'list-none m-0 p-0' }, team.needs_you.map((n) => h('li', { key: n.item_id, 'data-testid': 'need', 'data-why': n.why, className: 'flex flex-wrap items-center gap-2 py-1' },
        h(UI.Badge, { variant: n.why === 'merge' ? 'ok' : STATUS_TONE[n.why] }, t(`need_${n.why}`)), h('span', null, n.title),
        n.pr ? h('span', { className: MUTED }, t('prNum', { n: n.pr })) : null, h(SessionLink, { k: n.session, label: t('openSession') }))))
        : h('div', { className: MUTED, 'data-testid': 'no-needs' }, t('noNeeds'))),
    h(UI.Card, { key: 'tree' }, h('div', { className: LABEL }, t('treeTitle')),
      team.leads.map((r) => h(Reporter, { key: r.key, rec: r, staleMinutes: m }))),
    team.loose_lanes.length ? h(UI.Card, { key: 'loose' }, h('div', { className: LABEL }, t('looseTitle')),
      team.loose_lanes.map((r) => h(Reporter, { key: r.key, rec: r, staleMinutes: m }))) : null)
}

export const TABS = ['board', 'signals', 'rounds', 'prompts', 'team', 'settings']
const why = (e) => String(e?.message || e)
const clock = (s) => new Date(s * 1000).toLocaleTimeString(getLang(), { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
/** One line for the GitHub job (it runs for minutes after a refresh). */
export const jobText = (j) => (!j ? '' : j.running ? t('ghRunning', { at: clock(j.started_at) })
  : !j.finished_at ? t('ghNever') : j.error ? t('ghFailed', { at: clock(j.finished_at), e: j.error }) : t('ghDone', { n: j.rows, at: clock(j.finished_at) }))
/** One line for the latest post-merge regression run. */
export const regressText = (r) => (!r ? t('regNone') : t(r.regressions.length ? 'regBad' : 'regClean',
  { sha: r.sha.slice(0, 7), pass: r.counts.pass, n: r.counts.run, bad: r.regressions.length }))
/** One line for the PR scoring job. */
export const scoreJobText = (s) => (!s ? '' : s.running ? t('scoreRunning', { at: clock(s.started_at) })
  : !s.finished_at ? '' : s.error ? t('scoreFailed', { at: clock(s.finished_at), e: s.error }) : t('scoreDone', { at: clock(s.finished_at), n: s.updated.length }))
/** One line for the design-crew round (it runs for many minutes). */
export const roundText = (r) => (!r ? '' : r.running ? t('roundRunning', { n: r.round, at: clock(r.started_at) })
  : !r.finished_at ? t('roundNever') : r.error ? t('roundFailed', { n: r.round, at: clock(r.finished_at), e: r.error })
    : [t('roundDone', { n: r.round, p: r.counts.proposals, s: r.counts.signals, at: clock(r.finished_at) }), ...r.notes].join(' · '))
/** Run round, two steps: the first click arms, the second confirms. */
export function RunRound({ armed, running, onArm, onConfirm, onCancel }) {
  if (running) return h(UI.Btn, { type: 'button', disabled: true }, t('roundBusy'))
  if (!armed) return h(UI.Btn, { type: 'button', onClick: onArm, 'data-testid': 'round-arm' }, t('roundArm'))
  return h('span', { className: 'flex gap-2' },
    h(UI.Btn, { type: 'button', primary: true, onClick: onConfirm, 'data-testid': 'round-confirm' }, t('roundConfirm')),
    h(UI.Btn, { type: 'button', onClick: onCancel }, t('cancel')))
}

/** Read once, then poll every 5 s while the value says it is running. */
function usePolled(read, running, after) {
  const [v, set] = useState(undefined)
  useEffect(() => { read().then(set, () => set(null)) }, [read])
  useEffect(() => {
    if (!v || !running(v)) return undefined
    const id = setTimeout(() => read().then((n) => { set(n); if (!running(n)) after?.() }, () => set({ ...v })), 5000)
    return () => clearTimeout(id)
  }, [v, read])
  return [v, set]
}
const isRunning = (x) => !!x?.running, scoreRunning = (x) => !!x?.score?.running

/** The page body over one data source (backendSource or demoSource). */
export function HarnessRsi({ src, demo = false }) {
  const [tab, setTab] = useState('board')
  const [note, setNote] = useState('')
  const [[data, error], setState] = useState([null, ''])
  const reload = useCallback(() => src.load().then((d) => setState([d, '']), (e) => setState([null, why(e)])), [src])
  useEffect(() => { reload() }, [reload])
  const [changes, setChanges] = usePolled(src.promptChanges, () => false)
  const [team] = usePolled(src.team, () => false)
  const [out, setOut] = usePolled(src.outcomes, scoreRunning)
  const [job, setJob] = usePolled(src.status, isRunning, reload)
  const [round, setRound] = usePolled(src.round, isRunning, reload)
  const [regress] = usePolled(src.regress, () => false)
  const [armed, setArmed] = useState(false)
  const [[form, formNote], setForm] = useState([null, ''])
  useEffect(() => { src.settings().then((v) => setForm([toForm(v), '']), (e) => setForm([null, why(e)])) }, [src])
  const [[sched, schedNote], setSched] = useState([null, ''])
  useEffect(() => { src.schedule().then((v) => setSched([v, '']), () => {}) }, [src])
  const [[disp, dispNote], setDisp] = useState([null, ''])
  useEffect(() => { src.dispatchConf().then((v) => setDisp([v, '']), () => {}) }, [src])
  const failed = (key) => (e) => setNote(t(key, { e: why(e) })), notSaved = (e) => t('notSaved', { e: why(e) })
  const save = () => src.saveSettings(form).then((v) => setForm([toForm(v), t('saved')]), (e) => setForm([form, notSaved(e)]))
  const saveSched = () => src.saveSchedule(sched.schedule).then(() => src.schedule())
    .then((v) => setSched([v, t('saved')]), (e) => setSched([sched, notSaved(e)]))
  const saveDisp = () => src.saveDispatch(disp).then((v) => setDisp([v, t('saved')]), (e) => setDisp([disp, notSaved(e)]))
  // The card shows the choice at once; the reload then shows what decisions.jsonl holds.
  const decide = (id, decision) => {
    setState(([d]) => [{ ...d, proposals: applyDecision(d.proposals, id, decision) }, ''])
    Promise.resolve(src.decide(id, decision)).then(() => { setNote(t('savedDecision', { d: t(`dec_${decision}`) })); reload(); src.outcomes().then(setOut, () => {}) },
      (e) => { failed('decideFailed')(e); reload() })
  }
  const decidePrompt = (id, decision) => src.decidePrompt(id, decision)
    .then(() => { setNote(t(decision === 'do' ? 'pcDone' : 'saved')); src.promptChanges().then(setChanges, () => {}) }, failed('pcFailed'))
  const linkPr = (id, n) => src.link(id, n).then(() => src.outcomes().then(setOut), failed('linkFailed'))
  const score = () => src.score().then(setOut, failed('scoreStartFailed'))
  const runRound = () => { setArmed(false); src.runRound().then(setRound, failed('roundStartFailed')) }
  const refresh = () => {
    setNote(t('refreshing'))
    src.refresh().then((r) => { setNote([t('refreshed', { total: r.total, added: r.added }), ...r.errors].join(' · ')); setJob(r.github || null); reload() },
      failed('refreshFailed'))
  }

  const toolbar = (...c) => h('div', { className: 'flex flex-wrap items-center gap-2 mb-3' }, ...c)
  const lines = (...xs) => xs.filter(Boolean).map((x, i) => h('div', { key: i, className: MUTED }, x))
  const pending = (data?.proposals || []).filter((p) => !p.decision).length
  const panels = {
    board: () => [toolbar(h(UI.Btn, { type: 'button', onClick: score, disabled: scoreRunning(out) }, t('scorePrs')), h('span', { className: MUTED, 'data-testid': 'score-job' }, scoreJobText(out?.score))),
      h(Board, { proposals: data.proposals, images: data.images, signals: data.signals, onDecide: decide, outcomes: out?.outcomes, onLink: linkPr, dispatches: out?.dispatches })],
    signals: () => [toolbar(h(UI.Btn, { type: 'button', onClick: refresh }, t('refresh')), h('span', { className: MUTED, 'data-testid': 'github-job' }, jobText(job))),
      form && !form.saved.command_set ? h('div', { key: 's', className: `${MUTED} mb-3`, 'data-testid': 'slack-off' }, slackNote(null)) : null,
      h(Signals, { signals: data.signals })],
    rounds: () => [toolbar(h(RunRound, { armed, running: isRunning(round), onArm: () => setArmed(true), onConfirm: runRound, onCancel: () => setArmed(false) })),
      h(UI.Card, { key: 'jobs' }, h('div', { className: LABEL }, t('jobsTitle')),
        h('div', { 'data-testid': 'round-job' }, lines(roundText(round) || t('roundNever'))), h('div', { 'data-testid': 'regress' }, lines(regressText(regress))),
        lines(jobText(job), scoreJobText(out?.score))),
      h(UI.Card, { key: 'runs' }, h(Runs, { runs: sched?.runs || [] }))],
    team: () => [h(Team, { key: 't', team })],
    prompts: () => [(changes || []).length ? changes.map((c) => h(PromptChangeCard, { key: c.id, change: c, onDecide: decidePrompt })) : empty(Wand2, 'noPromptChanges')],
    settings: () => [form ? h(SettingsForm, { key: 'f', form, note: formNote, onSave: save, onChange: (f) => setForm([f, '']) }) : lines(formNote),
      sched ? h(ScheduleForm, { key: 's', conf: sched.schedule, note: schedNote, onSave: saveSched, onChange: (c) => setSched([{ ...sched, schedule: c }, '']) }) : null,
      disp ? h(DispatchForm, { key: 'd', conf: disp, note: dispNote, onSave: saveDisp, onChange: (c) => setDisp([c, '']) }) : null],
  }
  const body = error ? h(UI.ErrorNotice, { title: t('loadFailed'), message: error, testId: 'load-error' })
    : !data ? h(UI.ContentSkeleton, { rows: 4 }) : stack(...panels[tab]())
  const counts = { board: data?.proposals.length, signals: data?.signals.length, prompts: changes?.length }
  return h('div', { className: 'flex-1 min-w-0 flex flex-col min-h-0', lang: getLang() },
    h(UI.PageHeader, { title: t('title'), subtitle: t('subtitle') }),
    h('div', { className: 'flex-1 overflow-y-auto px-4 md:px-6 pb-8 min-h-0' }, h('div', { className: 'max-w-4xl flex flex-col gap-3' },
      demo ? h('div', { role: 'note', 'data-testid': 'demo-note', className: 'rounded-md border border-border bg-bg-elevated px-3 py-2.5 text-[12.5px] text-muted' }, t('demoNote')) : null,
      h('div', { className: 'grid grid-cols-2 sm:grid-cols-4 gap-3' },
        h(UI.StatCard, { label: t('statProposals'), value: data ? data.proposals.length : '—', accent: true }),
        h(UI.StatCard, { label: t('statPending'), value: data ? pending : '—' }),
        h(UI.StatCard, { label: t('statSignals'), value: data ? data.signals.length : '—' }),
        h(UI.StatCard, { label: t('statPrompts'), value: changes ? changes.length : '—' })),
      h(UI.SegmentedControl, { ariaLabel: t('tabsAria'), value: tab, onChange: setTab, wrap: true,
        segments: TABS.map((k) => ({ key: k, label: t(`tab_${k}`), ...(counts[k] != null ? { count: counts[k] } : {}) })) }),
      h('div', { className: MUTED, 'aria-live': 'polite', 'data-testid': 'note' }, note),
      h('div', { role: 'region', 'aria-label': t(`tab_${tab}`), 'data-testid': `panel-${tab}` }, body))))
}

/** The installed page: the backend is the data source, or the fixtures with `?demo=1`. */
export default function HarnessRsiPage() {
  sdk.useLanguageGeneration?.()
  setLang(pickLang(sdk.activeLocale?.()))
  const api = sdk.useAppApi()
  const demo = isDemo(globalThis.location?.search)
  const src = useMemo(() => (demo ? demoSource() : backendSource(api)), [api, demo])
  return h(HarnessRsi, { src, demo })
}
