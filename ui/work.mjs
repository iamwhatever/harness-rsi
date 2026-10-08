// The Work tab (docs/design/ux-v2.md §5.3) on live data: one row per card the owner picked, from pick to
// worker chat to PR to CI to merge to judge score, with the Team snapshots' stale / blocked flags and a link
// to the chat. PRs no known card owns come last. Reads GET /outcomes (rows, dispatches, ci) and GET /team.
import { createElement as h } from 'react'
import * as UI from '@kirocrew/app-sdk/ui'
import Lucide from 'lucide-react'
import { has, t } from './strings.mjs'
import { byHeat, prUrl, regressLine, scoreText, sessionHref } from './index.mjs'
import { Src } from './home.mjs'

const { ListChecks } = Lucide
const LABEL = 'text-[11px] uppercase tracking-wide text-muted font-semibold mb-1'
const MUTED = 'text-[13px] text-muted'
const stack = (...c) => h('div', { className: 'flex flex-col gap-3' }, ...c)
const row = (...c) => h('div', { className: 'flex flex-wrap items-center gap-2' }, ...c)
const prNum = (pr) => String(pr).split('#').pop()
const link = (href, label, testId) => h('a', { href, className: 'text-accent hover:underline', 'data-testid': testId,
  ...(href.startsWith('http') ? { target: '_blank', rel: 'noreferrer' } : {}) }, label)

/** Every work item in the Team snapshots, flat, with whether the snapshot that reported it is stale. */
export function teamItems(team) {
  const out = []
  const walk = (r) => (r.items || []).forEach((i) => { out.push({ ...i, reporter_stale: !!r.stale }); if (i.lane) walk(i.lane) })
  ;[...(team?.leads || []), ...(team?.loose_lanes || [])].forEach(walk)
  return out
}

/** The worker chat of a row: its dispatch row's session, else the one the outcome ledger kept. */
const sessionOf = (d, o) => d?.session || o?.dispatch?.session || null
/** The owner's choice for a card: its decision, else the one the outcome ledger copied. */
const choiceOf = (p, o) => p?.decision || o?.decision || null

/** One row per picked card (Do, or decided with a linked PR), hottest first; then PRs no known card owns. */
export function workRows({ proposals = [], out, team }) {
  const outs = out?.outcomes || [], sent = out?.dispatches || [], items = teamItems(team)
  const mine = (session, o) => items.filter((i) => (session && i.worker_session_key === session) || (o && i.pr != null && String(i.pr) === prNum(o.pr)))
  const ids = new Set(proposals.map((p) => p.id))
  const cards = byHeat(proposals).map((p) => ({ p, o: outs.find((o) => o.card_id === p.id), d: sent.find((d) => d.card_id === p.id) }))
    .filter(({ p, o }) => choiceOf(p, o) === 'do' || (choiceOf(p, o) && o))
  return [...cards.map(({ p, o, d }) => ({ id: p.id, title: p.pain, p, o, d, items: mine(sessionOf(d, o), o) })),
    ...outs.filter((o) => !ids.has(o.card_id)).map((o) => ({ id: o.pr, title: null, card: o.card_id, o, d: null, items: mine(sessionOf(null, o), o) }))]
}

const FLAG_TONE = { blocked: 'err', question: 'warn', stale: 'warn', orphaned: 'warn', failed: 'err' }
/** The row's flags: blocked / question from an open item, stale from the item or its snapshot, a failed dispatch. */
export function flags({ d, items = [] }) {
  const open = items.filter((i) => i.state === 'open')
  return [
    open.some((i) => i.status === 'blocked') && 'blocked',
    open.some((i) => i.status === 'question') && 'question',
    items.some((i) => i.stale || i.reporter_stale) && 'stale',
    items.some((i) => i.orphaned) && 'orphaned',
    d?.state === 'error' && 'failed',
  ].filter(Boolean)
}

const CI_DONE = { pass: true }
/** The six steps of one row: { k, done, text, href, bad }. ``ci`` is GET /outcomes' ``ci`` answer. */
export function steps({ p, o, d }, ci) {
  const choice = choiceOf(p, o), session = sessionOf(d, o)
  const state = o ? ci?.checks?.[o.pr] : null
  return [
    { k: 'picked', done: choice === 'do', text: choice ? t(`dec_${choice}`) : t('work_noCard') },
    { k: 'chat', done: !!session, href: session ? sessionHref(session) : null, bad: d?.state === 'error',
      text: session ? t('work_openChat') : d?.state === 'pending' ? t('dispOpening') : d?.state === 'error' ? t('work_chatFailed') : '—' },
    { k: 'pr', done: !!o, href: o ? prUrl(o.pr) : null, text: o ? `#${prNum(o.pr)} ${has(`state_${o.state}`) ? t(`state_${o.state}`) : o.state}` : '—' },
    { k: 'ci', done: !!CI_DONE[state], bad: state === 'fail', text: !o ? '—' : t(`work_ci_${state || 'unknown'}`) },
    { k: 'merged', done: o?.state === 'merged', text: o?.merged_sha ? o.merged_sha.slice(0, 7) : '—' },
    { k: 'judge', done: !!o?.score?.head, text: o ? scoreText(o) : '—' },
  ]
}

/** One work row: title, flags, the six steps, the regress line and what the worker last said about it. */
function WorkRow({ r, ci }) {
  const fl = flags(r), session = sessionOf(r.d, r.o)
  const said = r.items.filter((i) => i.summary).map((i) => i.summary)
  return h(UI.Card, { 'data-testid': 'work-row', 'data-id': r.id, 'data-flags': fl.join(' ') },
    row(h('div', { className: 'text-[15px] font-semibold text-text-strong min-w-0' },
      r.title || (r.card ? t('work_lostCard', { n: prNum(r.o.pr), id: r.card }) : t('work_noCardPr', { n: prNum(r.o.pr) }))),
    ...fl.map((f) => h(UI.Badge, { key: f, variant: FLAG_TONE[f], 'data-testid': 'work-flag', 'data-flag': f }, t(`work_flag_${f}`))),
    session ? link(sessionHref(session), t('work_openChat'), 'work-chat') : null),
    h('ol', { className: 'list-none m-0 p-0 mt-2 grid gap-2 grid-cols-2 sm:grid-cols-3 lg:grid-cols-6' }, steps(r, ci).map((s) =>
      h('li', { key: s.k, 'data-testid': 'work-step', 'data-step': s.k, 'data-done': s.done, className: 'min-w-0 rounded-md border border-border px-2 py-1.5' },
        h('div', { className: LABEL }, t(`work_step_${s.k}`)),
        h('div', { className: `text-[13px] ${s.bad ? 'text-danger' : s.done ? 'text-text-strong' : 'text-muted'} break-words` }, s.href ? link(s.href, s.text) : s.text)))),
    r.o && regressLine(r.o) ? h('div', { className: `${MUTED} mt-2` }, regressLine(r.o)) : null,
    said.length ? h('div', { className: `${MUTED} mt-2`, 'data-testid': 'work-said' }, t('work_said', { s: said.join(' · ') })) : null)
}

/** The Work tab body: the Score PRs button, where CI and flags come from, then the rows. */
export function Work({ proposals, out, team, onScore, scoreBusy, scoreLine }) {
  const rows = workRows({ proposals, out, team }), ci = out?.ci
  const head = h(UI.Card, { 'data-testid': 'work-head' },
    row(h(UI.Btn, { type: 'button', onClick: onScore, disabled: !!scoreBusy, 'data-testid': 'work-score' }, t('scorePrs')),
      h('span', { className: MUTED, 'data-testid': 'score-job' }, scoreLine)),
    h('div', { className: MUTED }, t('work_note'), ' ', h(Src, { route: '/outcomes', when: ci?.read_at })),
    h('div', { className: MUTED }, t('work_flagsNote'), ' ', h(Src, { route: '/team' })),
    ci?.error ? h('div', { className: 'text-[13px] text-danger', role: 'alert', 'data-testid': 'work-ci-error' }, t('work_ciError', { e: ci.error })) : null)
  if (!rows.length) return stack(head, h(UI.EmptyState, { icon: h(ListChecks, { size: 28 }), title: t('work_empty'), subtitle: t('work_emptyNext'), testId: 'work-empty' }))
  return stack(head, ...rows.map((r) => h(WorkRow, { key: r.id, r, ci })))
}
