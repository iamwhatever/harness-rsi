// UX v2 MOCKUP (docs/design/ux-v2.md): Home / Needs you / Work / Settings, plus folded Details.
// Shown only with ?demo=1&ux=v2, over the fixtures; the current page stays the default. Host components
// only; every word on screen comes from strings.mjs. Writes go to demoSource, which keeps them in memory.
import { createElement as h, useCallback, useEffect, useState } from 'react'
import * as UI from '@kirocrew/app-sdk/ui'
import Lucide from 'lucide-react'
import { getLang, has, t } from './strings.mjs'
import { RunRound, Runs, Signals, byHeat, prUrl, scoreText, sessionHref, regressLine } from './index.mjs'

const { Inbox, ListChecks } = Lucide
export const VIEWS = ['home', 'needs', 'work', 'settings']
/** The v2 mockup is a second explicit flag on top of demo mode. */
export const isUxV2 = (search) => new URLSearchParams(search || '').get('ux') === 'v2'

const LABEL = 'text-[11px] uppercase tracking-wide text-muted font-semibold mb-1'
const MUTED = 'text-[13px] text-muted'
const at = (s) => (s ? new Date(typeof s === 'number' ? s * 1000 : s).toLocaleString(getLang(), { dateStyle: 'short', timeStyle: 'short', hourCycle: 'h23' }) : '')
const stack = (...c) => h('div', { className: 'flex flex-col gap-3' }, ...c)
const row = (...c) => h('div', { className: 'flex flex-wrap items-center gap-2' }, ...c)
const card = (title, ...c) => h(UI.Card, null, h('div', { className: LABEL }, title), ...c)
/** Where a number comes from: the route (as code, not prose) and when it was read or made. */
const Src = ({ route, when }) => h('span', { className: `${MUTED} text-[12px]`, 'data-testid': 'src' },
  t('v2_src'), ' ', h('code', null, route), when ? ` · ${at(when)}` : '')
/** What a button does and what it costs, next to it. */
const Cost = ({ k, vars }) => h('span', { className: MUTED, 'data-testid': 'cost-note' }, t(k, vars))
const link = (href, label, testId) => h('a', { href, className: 'text-accent hover:underline', 'data-testid': testId,
  ...(href.startsWith('http') ? { target: '_blank', rel: 'noreferrer' } : {}) }, label)
const prNum = (pr) => pr.split('#').pop()

/** The next scheduled round as the page can work it out (gap: the backend gives no next_at). */
export const nextRound = (s) => (s.round_enabled ? t('v2_nextAt', { day: t('days').split(',')[s.weekday], hour: String(s.hour).padStart(2, '0') }) : t('v2_schedOff'))

/** The setup checklist, in order; the first open step is Home's primary action. */
export const checklist = ({ settings, sched, disp }) => [
  { key: 'slack', ok: !!settings?.command_set, route: '/settings' },
  { key: 'dir', ok: !!sched?.schedule.kirocrew_dir, route: '/schedule' },
  { key: 'repos', ok: !!disp?.repos.length, route: '/dispatch' },
  { key: 'sched', ok: !!sched?.schedule.round_enabled, route: '/schedule' },
]

/** Cards whose decision is open, and only those, are in Needs you; a decided card with work is in Work. */
export function queue({ proposals, out, disp, changes, team, applied }) {
  const sent = out?.dispatches || []
  const live = (id) => sent.find((d) => d.card_id === id && d.state !== 'error') || out?.outcomes.find((o) => o.card_id === id && o.dispatch)
  const used = sent.filter((d) => d.state !== 'error').length
  const left = Math.max(0, (disp?.daily_cap ?? 0) - used)
  return [
    ...byHeat(proposals.filter((p) => !p.decision)).map((p) => ({ kind: 'pick', id: p.id, p })),
    ...byHeat(proposals.filter((p) => p.decision === 'do' && !live(p.id))).map((p) => ({ kind: 'start', id: p.id, p, left })),
    ...(changes || []).filter((c) => c.status === 'pending' && !applied.includes(c.id)).map((c) => ({ kind: 'prompt', id: c.id, c })),
    ...(team?.needs_you || []).map((n) => ({ kind: n.why, id: n.item_id, n })),
  ]
}

/** One row per decided card that has work, plus PRs no card owns. */
export function workRows({ proposals, out }) {
  const outs = out?.outcomes || [], sent = out?.dispatches || []
  const cards = byHeat(proposals.filter((p) => p.decision && (p.decision === 'do' || outs.some((o) => o.card_id === p.id))))
  return [...cards.map((p) => ({ id: p.id, title: p.pain, p, o: outs.find((o) => o.card_id === p.id), d: sent.find((d) => d.card_id === p.id) })),
    ...outs.filter((o) => !o.card_id).map((o) => ({ id: o.pr, title: null, o }))]
}

const STEPS = ['picked', 'chat', 'pr', 'ci', 'merged', 'judge']
/** The six steps of one Work row: [step, done, text, href]. CI is a gap: the backend has no check state. */
export function steps({ p, o, d }) {
  const session = d?.session || o?.dispatch?.session
  return [
    ['picked', p?.decision === 'do', p ? t(`dec_${p.decision}`) : t('v2_noCard')],
    ['chat', !!session, session ? t('v2_openChat') : '—', session ? sessionHref(session) : null],
    ['pr', !!o?.pr, o?.pr ? `#${prNum(o.pr)} ${has(`state_${o.state}`) ? t(`state_${o.state}`) : o.state}` : '—', o?.pr ? prUrl(o.pr) : null],
    ['ci', false, t('v2_gap')],
    ['merged', o?.state === 'merged', o?.merged_sha ? o.merged_sha.slice(0, 7) : '—'],
    ['judge', !!o?.score.head, o ? scoreText(o) : '—'],
  ]
}

function Home({ round, sched, settings, disp, needs, signals, changes, go, armed, setArmed, runRound }) {
  const list = checklist({ settings, sched, disp })
  const open = list.find((x) => !x.ok)
  const s = sched?.schedule
  const primary = open ? h(UI.Btn, { type: 'button', primary: true, 'data-testid': 'primary', onClick: () => go('settings') }, t(`v2_do_${open.key}`))
    : needs ? h(UI.Btn, { type: 'button', primary: true, 'data-testid': 'primary', onClick: () => go('needs') }, t('v2_goNeeds', { n: needs }))
      : null
  return stack(
    h(UI.Card, { 'data-testid': 'v2-status' }, h('div', { className: LABEL }, t('v2_statusTitle')),
      h('div', { className: 'text-[15px] text-text-strong' }, round?.running ? t('v2_running', { n: round.round, at: at(round.started_at) }) : t('v2_idle')),
      h('div', null, round?.finished_at ? t(round.error ? 'v2_lastFailed' : 'v2_last', { n: round.round, s: round.counts?.signals ?? 0, p: round.counts?.proposals ?? 0, at: at(round.finished_at), e: round.error }) : t('v2_lastNever'), ' ', h(Src, { route: '/round/status' })),
      s ? h('div', { 'data-testid': 'v2-next' }, t('v2_next', { when: nextRound(s) }), ' ', h(Src, { route: '/schedule' })) : null,
      s ? row(...['round_enabled', 'regress_enabled', 'score_enabled'].map((k) => h(UI.Badge, { key: k, variant: s[k] ? 'ok' : 'muted' }, t(`v2_sw_${k}`, { on: t(s[k] ? 'v2_on' : 'v2_off') })))) : null),
    h(UI.Card, { 'data-testid': 'v2-setup' }, h('div', { className: LABEL }, t('v2_setupTitle', { done: list.filter((x) => x.ok).length, n: list.length })),
      h('ul', { className: 'list-none m-0 p-0' }, list.map((x) => h('li', { key: x.key, 'data-testid': 'setup-step', 'data-ok': x.ok, className: 'flex flex-wrap items-center gap-2 py-1' },
        h(UI.Badge, { variant: x.ok ? 'ok' : 'warn' }, t(x.ok ? 'v2_stepDone' : 'v2_stepOpen')), h('span', null, t(`v2_step_${x.key}`)), h(Src, { route: x.route }))))),
    h(UI.Card, { 'data-testid': 'v2-act' }, h('div', { className: LABEL }, t('v2_actTitle')),
      primary ? row(primary, open ? h(Cost, { k: 'v2_costSetup' }) : h(Cost, { k: 'v2_costNeeds' })) : null,
      row(h(RunRound, { armed, running: !!round?.running, onArm: () => setArmed(true), onConfirm: runRound, onCancel: () => setArmed(false) }), h(Cost, { k: 'v2_costRound' }))),
    h('details', { 'data-testid': 'v2-details', className: 'rounded-md border border-border px-3 py-2' },
      h('summary', { className: MUTED }, t('v2_detailsTitle')),
      stack(h('div', { className: MUTED }, t('v2_detailsSignals'), ' ', h(Src, { route: '/signals' })), h(Signals, { signals }),
        h(UI.Card, null, h(Runs, { runs: sched?.runs || [] }), h(Src, { route: '/schedule' })),
        ...(changes || []).filter((c) => c.ab).map((c) => h(UI.Card, { key: c.id, 'data-testid': 'v2-raw-ab' }, h('div', { className: LABEL }, t('v2_rawAb', { agent: c.agent })),
          h('div', { className: 'overflow-x-auto' }, h('table', { className: 'w-full border-collapse text-[13px]' }, h('thead', null, h('tr', null, ['colMetric', 'colA', 'colB', 'colVerdict'].map((k) => h('th', { key: k, scope: 'col', className: 'text-left px-2 py-1' }, t(k))))),
            h('tbody', null, Object.entries(c.ab.metrics).map(([m, v]) => h('tr', { key: m }, h('td', { className: 'px-2 py-1' }, h('code', null, m)),
              h('td', { className: 'px-2 py-1' }, String(v.A ?? '—')), h('td', { className: 'px-2 py-1' }, String(v.B ?? '—')), h('td', { className: 'px-2 py-1' }, t(`verdict_${v.verdict}`))))))))))))
}

const KIND_TONE = { pick: 'aim', start: 'aim', prompt: 'warn', question: 'warn', blocked: 'err', merge: 'ok' }
/** One Needs-you item: why it is here, the evidence, and one action. */
function NeedItem({ item: x, signals, repo, onPick, onSkip, onStart, onApply }) {
  const head = (title) => row(h(UI.Badge, { variant: KIND_TONE[x.kind] || 'muted' }, t(`v2_kind_${x.kind}`)), h('span', { className: 'text-text-strong' }, title))
  let body
  if (x.kind === 'pick' || x.kind === 'start') {
    const p = x.p
    const srcs = signals.filter((s) => p.signal_ids.includes(s.id))
    body = [head(p.pain), h('div', { className: MUTED }, t(`v2_why_${x.kind}`)),
      row(h('span', null, t('heat', { people: p.heat.people, days: p.heat.window_days })), h('span', null, t('cost', { files: p.cost.files, lines: p.cost.lines })),
        ...srcs.map((s) => link(s.links[0], s.source)), h(Src, { route: '/proposals' })),
      x.kind === 'pick'
        ? row(h(UI.Btn, { type: 'button', primary: true, 'data-testid': 'need-action', onClick: () => onPick(p.id) }, t('v2_actPick')), h(Cost, { k: 'v2_costPick' }),
          h(UI.Btn, { type: 'button', 'data-testid': 'need-skip', onClick: () => onSkip(p.id) }, t('dec_skip')))
        : row(h(UI.Btn, { type: 'button', primary: true, 'data-testid': 'need-action', disabled: !x.left, onClick: () => onStart(p.id) }, t('v2_actStart')), h(Cost, { k: 'v2_costStart', vars: { left: x.left } }))]
  } else if (x.kind === 'prompt') {
    const c = x.c
    body = [head(`${c.agent}: ${c.summary}`), h('div', { className: MUTED }, t('v2_why_prompt')),
      c.ab ? h('div', null, Object.entries(c.ab.metrics).map(([m, v]) => h('div', { key: m }, h('code', null, m), ` ${v.A ?? '—'} → ${v.B ?? '—'} `, h(UI.Badge, { variant: v.verdict === 'better' ? 'ok' : v.verdict === 'worse' ? 'err' : 'muted' }, t(`verdict_${v.verdict}`)))),
        h('div', { className: MUTED, 'data-testid': 'ab-source' }, t('v2_abFrom', { rounds: c.ab.rounds.join(', '), reps: c.ab.reps }), ' ', h('code', null, 'crew/ab.py'), ' · ', t('v2_proposedAt', { at: at(c.at) })))
        : h('div', { className: MUTED }, t('noAb')),
      row(h(UI.Btn, { type: 'button', primary: true, 'data-testid': 'need-action', onClick: () => onApply(c.id) }, t('v2_actApply')), h(Cost, { k: 'v2_costApply', vars: { agent: c.agent } }))]
  } else {
    const n = x.n
    body = [head(n.title), h('div', { className: MUTED }, t(`v2_why_${x.kind}`), ' ', h(Src, { route: '/team' })),
      row(x.kind === 'merge' && n.pr && repo ? link(prUrl(`${repo}#${n.pr}`), t('v2_actOpenPr', { n: n.pr }), 'need-action')
        : link(sessionHref(n.session), t('v2_openChat'), 'need-action'), h(Cost, { k: x.kind === 'merge' ? 'v2_costMerge' : 'v2_costChat' }))]
  }
  return h(UI.Card, { 'data-testid': 'need-item', 'data-kind': x.kind, 'data-id': x.id }, ...body)
}

function Needs({ items, nextWhen, ...on }) {
  if (!items.length) return h(UI.EmptyState, { icon: h(Inbox, { size: 28 }), title: t('v2_needsEmpty'), subtitle: t('v2_needsEmptyNext', { when: nextWhen }), testId: 'v2-needs-empty' })
  return stack(h('div', { role: 'note', className: MUTED }, t('v2_needsNote')), ...items.map((x) => h(NeedItem, { key: `${x.kind}-${x.id}`, item: x, ...on })))
}

function Work({ rows, go }) {
  if (!rows.length) return h(UI.EmptyState, { icon: h(ListChecks, { size: 28 }), title: t('v2_workEmpty'), subtitle: t('v2_workEmptyNext'), testId: 'v2-work-empty' })
  return stack(h('div', { className: MUTED }, t('v2_workNote'), ' ', h(Src, { route: '/outcomes' })),
    ...rows.map((r) => h(UI.Card, { key: r.id, 'data-testid': 'work-row', 'data-id': r.id },
      h('div', { className: 'text-[15px] font-semibold text-text-strong' }, r.title || t('v2_noCardPr', { n: prNum(r.o.pr) })),
      h('ol', { className: 'list-none m-0 p-0 mt-2 grid gap-2 sm:grid-cols-3 lg:grid-cols-6' }, steps(r).map(([k, done, text, href]) =>
        h('li', { key: k, 'data-testid': 'work-step', 'data-step': k, 'data-done': done, className: 'min-w-0 rounded-md border border-border px-2 py-1.5' },
          h('div', { className: LABEL }, t(`v2_ws_${k}`)),
          h('div', { className: `text-[13px] ${done ? 'text-text-strong' : 'text-muted'} break-words` }, href ? link(href, text) : text)))),
      r.o && regressLine(r.o) ? h('div', { className: `${MUTED} mt-2` }, regressLine(r.o)) : null)),
    h('div', { className: MUTED }, t('v2_ciGap')))
}

function Settings({ settings, sched, disp, job }) {
  const s = sched?.schedule
  const item = (key, ok, state, how, route) => h('li', { key, 'data-testid': 'v2-setting', 'data-ok': ok, className: 'py-1.5' },
    row(h(UI.Badge, { variant: ok ? 'ok' : ok == null ? 'muted' : 'warn' }, state), h('span', { className: 'text-text-strong' }, t(`v2_set_${key}`)), route ? h(Src, { route }) : null),
    h('div', { className: MUTED }, how))
  const onOff = (v) => t(v ? 'v2_on' : 'v2_off')
  return stack(h('div', { role: 'note', className: MUTED }, t('v2_setNote')),
    card(t('v2_setSources'), h('ul', { className: 'list-none m-0 p-0' },
      item('slack', !!settings?.command_set, t(settings?.command_set ? 'v2_connected' : 'v2_notSet'),
        settings?.command_set ? t('slackOn', { n: settings.channels.length, days: settings.window_days }) : t('v2_howSlack'), '/settings'),
      item('github', !!job?.finished_at, job?.finished_at ? t('v2_connected') : t('v2_neverRead'), job?.finished_at ? t('ghDone', { n: job.rows, at: at(job.finished_at) }) : t('v2_howGithub'), '/refresh/status'),
      item('sessions', null, t('v2_gap'), t('v2_howSessions')))),
    card(t('v2_setSchedule'), h('ul', { className: 'list-none m-0 p-0' },
      item('round', !!s?.round_enabled, onOff(s?.round_enabled), nextRound(s || {}), '/schedule'),
      item('regress', !!s?.regress_enabled, onOff(s?.regress_enabled), t('v2_howRegress'), '/schedule'),
      item('score', !!s?.score_enabled, onOff(s?.score_enabled), t('v2_howScore'), '/schedule'),
      item('dir', !!s?.kirocrew_dir, t(s?.kirocrew_dir ? 'v2_connected' : 'v2_notSet'), s?.kirocrew_dir ? h('code', null, s.kirocrew_dir) : t('v2_howDir'), '/schedule'))),
    card(t('v2_setTrust'), h('ul', { className: 'list-none m-0 p-0' },
      item('trust', !disp?.trust_dispatched, onOff(disp?.trust_dispatched), t('dispTrustRisk'), '/dispatch'))),
    card(t('v2_setCaps'), h('ul', { className: 'list-none m-0 p-0' },
      item('cap', true, String(disp?.daily_cap ?? '—'), t('v2_howCap'), '/dispatch'),
      item('repos', !!disp?.repos.length, t(disp?.repos.length ? 'v2_connected' : 'v2_notSet'), (disp?.repos || []).join(', ') || t('v2_howRepos'), '/dispatch'),
      item('auto', null, onOff(disp?.auto_dispatch), t('v2_howAuto'), '/dispatch'))))
}

const why = (e) => String(e?.message || e)
/** The v2 mockup page over one data source (demoSource only: see index.mjs). */
export function HarnessRsiV2({ src }) {
  const [view, setView] = useState('home')
  const [note, setNote] = useState('')
  const [st, setSt] = useState(null)
  const [armed, setArmed] = useState(false)
  const [applied, setApplied] = useState([])
  const load = useCallback(() => Promise.all([src.load(), src.round(), src.schedule(), src.settings(), src.dispatchConf(), src.outcomes(), src.promptChanges(), src.team(), src.status()])
    .then(([data, round, sched, settings, disp, out, changes, team, job]) => setSt({ ...data, round, sched, settings, disp, out, changes, team, job }), (e) => setSt({ error: why(e) })), [src])
  useEffect(() => { load() }, [load])
  if (st?.error) return h(UI.ErrorNotice, { title: t('loadFailed'), message: st.error, testId: 'load-error' })
  const items = st ? queue({ ...st, applied }) : []
  const rows = st ? workRows(st) : []
  const done = (k, vars) => () => { setNote(t(k, vars)); return load() }
  const on = {
    signals: st?.signals || [], repo: st?.disp?.repos.length === 1 ? st.disp.repos[0] : null,
    onPick: (id) => src.decide(id, 'do').then(done('v2_notePicked')),
    onSkip: (id) => src.decide(id, 'skip').then(done('savedDecision', { d: t('dec_skip') })),
    onStart: (id) => src.dispatchCards([id]).then((r) => done(r.results[0]?.result === 'started' ? 'v2_noteStarted' : 'v2_noteNotStarted')()),
    onApply: (id) => src.decidePrompt(id, 'do').then(() => { setApplied((xs) => [...xs, id]); setNote(t('v2_noteApplied')) }),
  }
  const runRound = () => { setArmed(false); src.runRound().then(done('v2_noteRound')) }
  const go = (v) => setView(v)
  const panels = {
    home: () => h(Home, { round: st.round, sched: st.sched, settings: st.settings, disp: st.disp, needs: items.length, signals: st.signals, changes: st.changes, go, armed, setArmed, runRound }),
    needs: () => h(Needs, { items, nextWhen: st.sched ? nextRound(st.sched.schedule) : '', ...on }),
    work: () => h(Work, { rows, go }),
    settings: () => h(Settings, { settings: st.settings, sched: st.sched, disp: st.disp, job: st.job }),
  }
  const counts = { needs: st ? items.length : null, work: st ? rows.length : null }
  return h('div', { className: 'flex-1 min-w-0 flex flex-col min-h-0', lang: getLang(), 'data-testid': 'ux-v2' },
    h(UI.PageHeader, { title: t('title'), subtitle: t('v2_subtitle') }),
    h('div', { className: 'flex-1 overflow-y-auto px-4 md:px-6 pb-8 min-h-0' }, h('div', { className: 'max-w-4xl flex flex-col gap-3' },
      h('div', { role: 'note', 'data-testid': 'demo-note', className: 'rounded-md border border-border bg-bg-elevated px-3 py-2.5 text-[12.5px] text-muted' }, t('v2_demoNote')),
      h(UI.SegmentedControl, { ariaLabel: t('tabsAria'), value: view, onChange: setView, wrap: true,
        segments: VIEWS.map((k) => ({ key: k, label: t(`v2_view_${k}`), ...(counts[k] != null ? { count: counts[k] } : {}) })) }),
      h('div', { className: MUTED, 'aria-live': 'polite', 'data-testid': 'note' }, note),
      h('div', { role: 'region', 'aria-label': t(`v2_view_${view}`), 'data-testid': `v2-panel-${view}` }, st ? panels[view]() : h(UI.ContentSkeleton, { rows: 4 })))))
}
