// The Needs you tab (docs/design/ux-v2.md §5.2) on live data: ONE queue of everything waiting on the owner.
// Each item says why it is here, its evidence (with the route it came from and a time) and one action.
// Order: cards to pick, Do cards with no worker chat, prompt changes, then what worker chats ask (self-reported).
// Words come from strings.mjs (the needs_* block).
import { createElement as h } from 'react'
import * as UI from '@kirocrew/app-sdk/ui'
import Lucide from 'lucide-react'
import { has, t } from './strings.mjs'
import { DispatchResult, byHeat, dispatchable, prUrl, sessionHref } from './index.mjs'
import { Src, at, nextLine } from './home.mjs'

const { Inbox } = Lucide
const MUTED = 'text-[13px] text-muted'
const row = (...c) => h('div', { className: 'flex flex-wrap items-center gap-2 mt-2' }, ...c)
const link = (href, label, testId) => h('a', { href, 'data-testid': testId, className: 'text-accent hover:underline',
  ...(href.startsWith('http') ? { target: '_blank', rel: 'noreferrer' } : {}) }, label)
const Does = ({ k, vars }) => h('span', { className: MUTED, 'data-testid': 'need-does' }, t(k, vars))

/** Every item of the Team tree by id, so a needs_you entry can show its own summary and report time. */
function teamItems(team) {
  const out = {}
  const walk = (r) => (r?.items || []).forEach((i) => { out[i.item_id] = { ...i, reported: i.last_report_at || r.received_at }; walk(i.lane) })
  ;[...(team?.leads || []), ...(team?.loose_lanes || [])].forEach(walk)
  return out
}

/** How many dispatches are left today: ``daily_cap - used_today`` from ``GET /dispatch``; null when not read. */
export const leftToday = (disp) => (disp && disp.used_today != null ? Math.max(0, disp.daily_cap - disp.used_today) : null)

/** The queue, in order. A card is here only while it waits on you; once a chat is open it leaves (Work shows it). */
export function needsQueue({ proposals = [], out, disp, changes = [], team }) {
  const sent = out?.dispatches || []
  const tree = teamItems(team)
  const left = leftToday(disp)
  return [
    ...byHeat(proposals.filter((p) => !p.decision)).map((p) => ({ kind: 'pick', id: p.id, p })),
    ...byHeat(proposals.filter((p) => dispatchable(p, sent.find((d) => d.card_id === p.id))))
      .map((p) => ({ kind: 'start', id: p.id, p, d: sent.find((d) => d.card_id === p.id), left })),
    ...changes.filter((c) => c.status === 'pending').map((c) => ({ kind: 'prompt', id: c.id, c })),
    ...(team?.needs_you || []).map((n) => ({ kind: n.why, id: n.item_id, n, i: tree[n.item_id] })),
  ]
}

/** The A/B line of a prompt change: what ran (offline replay, rounds, reps), when, and the command. */
export function abSource(ab) {
  return [t('needs_abFrom', { rounds: ab.rounds.join(', '), reps: ab.reps }), ' ',
    ab.ran_at ? t('needs_abRan', { at: at(ab.ran_at) }) : t('needs_abRanUnknown'), ' ',
    h('code', { key: 'cmd' }, ab.command || 'crew/ab.py')]
}

const TONE = { pick: 'aim', start: 'aim', prompt: 'warn', question: 'warn', blocked: 'err', merge: 'ok' }
const VERDICT = { better: 'ok', worse: 'err', same: 'muted' }
const verdictWord = (v) => (has(`verdict_${v}`) ? t(`verdict_${v}`) : v)

function cardParts(x, { signals, readAt, results, onDecide, onDispatch }) {
  const p = x.p
  const srcs = signals.filter((s) => p.signal_ids.includes(s.id))
  const evidence = row(h('span', null, t('heat', { people: p.heat.people, days: p.heat.window_days })), h('span', null, t('cost', { files: p.cost.files, lines: p.cost.lines })),
    ...srcs.map((s) => link(s.links[0], s.source)), h(Src, { route: '/proposals', when: readAt }))
  if (x.kind === 'pick') {
    return [p.pain, evidence, row(
      h(UI.Btn, { type: 'button', primary: true, 'data-testid': 'need-action', onClick: () => onDecide(p.id, 'do') }, t('dec_do')),
      h(UI.Btn, { type: 'button', 'data-testid': 'need-skip', onClick: () => onDecide(p.id, 'skip') }, t('dec_skip')),
      h(UI.Btn, { type: 'button', 'data-testid': 'need-later', onClick: () => onDecide(p.id, 'later') }, t('dec_later')),
      h(Does, { k: 'needs_doesPick' }))]
  }
  const failed = x.d?.state === 'error' ? h('div', { className: 'text-[13px] text-danger mt-1', 'data-testid': 'need-failed' },
    t('needs_lastFailed', { at: at(x.d.at), e: x.d.error }), ' ', h(Src, { route: '/outcomes', when: x.d.at })) : null
  return [p.pain, evidence, failed, row(
    h(UI.Btn, { type: 'button', primary: true, 'data-testid': 'need-action', disabled: x.left === 0, onClick: () => onDispatch([p.id]) }, t('needs_actStart')),
    h(Does, { k: x.left == null ? 'needs_doesStartUnknown' : 'needs_doesStart', vars: { left: x.left } }), h(Src, { route: '/dispatch' })),
  results[p.id] ? h(DispatchResult, { result: results[p.id] }) : null]
}

function promptParts(c, { onPrompt }) {
  const evidence = c.ab ? [
    h('div', { key: 'm', className: 'mt-2' }, Object.entries(c.ab.metrics).map(([m, v]) => h('div', { key: m, 'data-testid': 'need-metric' },
      h('code', null, m), ` ${v.A ?? '—'} → ${v.B ?? '—'} `, h(UI.Badge, { variant: VERDICT[v.verdict] || 'muted' }, verdictWord(v.verdict))))),
    h('div', { key: 's', className: MUTED, 'data-testid': 'ab-source' }, ...abSource(c.ab))]
    : [h('div', { key: 'n', className: MUTED }, t('noAb'))]
  return [`${c.agent}: ${c.summary}`, ...evidence,
    h('div', { className: MUTED }, t('needs_proposed', { at: at(c.at) }), ' ', h(Src, { route: '/prompt-changes', when: c.at })),
    row(h(UI.Btn, { type: 'button', primary: true, 'data-testid': 'need-action', onClick: () => onPrompt(c.id, 'do') }, t('needs_actApply')),
      h(UI.Btn, { type: 'button', 'data-testid': 'need-skip', onClick: () => onPrompt(c.id, 'skip') }, t('dec_skip')),
      h(Does, { k: 'needs_doesApply', vars: { agent: c.agent } }))]
}

function teamParts(x, { repo }) {
  const { n, i } = x
  const pr = x.kind === 'merge' && n.pr && repo
  return [n.title,
    i?.summary ? h('div', { className: 'mt-1' }, i.summary) : null,
    h('div', { className: MUTED }, t('needs_reported', { who: n.reporter, at: at(i?.reported) }), ' ', h(Src, { route: '/team', when: i?.reported })),
    row(pr ? link(prUrl(`${repo}#${n.pr}`), t('needs_actOpenPr', { n: n.pr }), 'need-action') : link(sessionHref(n.session), t('openSession'), 'need-action'),
      h(Does, { k: x.kind === 'merge' ? 'needs_doesMerge' : 'needs_doesChat' }))]
}

/** One queue item: kind badge and title, why it is here, the evidence and one action. */
export function NeedItem({ item: x, ...ctx }) {
  const [title, ...rest] = x.kind === 'pick' || x.kind === 'start' ? cardParts(x, ctx) : x.kind === 'prompt' ? promptParts(x.c, ctx) : teamParts(x, ctx)
  return h(UI.Card, { 'data-testid': 'need-item', 'data-kind': x.kind, 'data-id': x.id },
    h('div', { className: 'flex flex-wrap items-center gap-2' }, h(UI.Badge, { variant: TONE[x.kind] || 'muted' }, t(`needs_kind_${x.kind}`)),
      h('span', { className: 'text-text-strong' }, title)),
    h('div', { className: MUTED, 'data-testid': 'need-why' }, t(`needs_why_${x.kind}`)),
    ...rest.filter(Boolean))
}

/** The Needs you tab body. ``readAt`` is when the page read the routes that carry no time of their own. */
export function Needs({ proposals, signals = [], out, disp, changes, team, sched, readAt, results = {}, onDecide, onDispatch, onPrompt }) {
  const items = needsQueue({ proposals, out, disp, changes: changes || [], team })
  if (!items.length) {
    return h(UI.EmptyState, { icon: h(Inbox, { size: 28 }), title: t('needs_empty'), subtitle: sched ? nextLine(sched) : t('home_unread'), testId: 'needs-empty' })
  }
  const repo = disp?.repos?.length === 1 ? disp.repos[0] : null
  return h('div', { className: 'flex flex-col gap-3' },
    h('div', { role: 'note', className: MUTED, 'data-testid': 'needs-note' }, t('needs_note', { n: items.length })),
    ...items.map((x) => h(NeedItem, { key: `${x.kind}-${x.id}`, item: x, signals, readAt, results, repo, onDecide, onDispatch, onPrompt })))
}
