// The Home tab (docs/design/ux-v2.md §5.1) on live data: is it running, the setup checklist, one primary
// action (Run round, with what it does and what it costs), and folded Details: signals, runs and jobs, raw A/B, the Team tree. Every number names its
// source (the route it came from) and a time; every empty state names the next step. Words come from strings.mjs.
import { createElement as h } from 'react'
import * as UI from '@kirocrew/app-sdk/ui'
import { getLang, t } from './strings.mjs'
import { RunRound, Runs, Signals, Team, jobText, regressText, roundText, slackNote } from './index.mjs'

const LABEL = 'text-[11px] uppercase tracking-wide text-muted font-semibold mb-1'
const MUTED = 'text-[13px] text-muted'
const row = (...c) => h('div', { className: 'flex flex-wrap items-center gap-2' }, ...c)
const stack = (...c) => h('div', { className: 'flex flex-col gap-3' }, ...c)
/** A date (ISO string, epoch seconds or ms) as the page's short local date and time. */
export const at = (s) => (s == null || s === '' ? '' : new Date(typeof s === 'number' && s < 1e12 ? s * 1000 : s)
  .toLocaleString(getLang(), { dateStyle: 'short', timeStyle: 'short', hourCycle: 'h23' }))
/** Where a value comes from: the route (as code) and when it was read or made. */
export const Src = ({ route, when }) => h('span', { className: `${MUTED} text-[12px]`, 'data-testid': 'src', 'data-route': route },
  t('home_src'), ' ', h('code', null, route), when ? ` · ${at(when)}` : '')
/** Seconds as minutes, or hours and minutes. */
export const duration = (s) => (s < 3600 ? t('home_min', { m: Math.max(1, Math.round(s / 60)) })
  : t('home_hm', { h: Math.floor(s / 3600), m: Math.round((s % 3600) / 60) }))
/** The cost line for Run round: the last-3 average (``round_stats``), or that no round has been timed. */
export const roundCost = (st) => (!st?.n ? t('home_costNone')
  : t('home_costAvg', { n: st.n, d: duration(st.avg_duration_s), c: st.avg_cost == null ? t('home_costUnknown') : t('home_credits', { c: st.avg_cost }) }))

const ROUND_KINDS = ['round', 'manual_round']
/** The newest finished round: the live job if it has ended, else the newest round run row. */
export function lastRound(round, runs = []) {
  if (round?.finished_at) return { n: round.round, s: round.counts?.signals ?? 0, p: round.counts?.proposals ?? 0, at: round.finished_at, e: round.error, route: '/round/status' }
  const r = runs.find((x) => ROUND_KINDS.includes(x.kind))
  return r ? { n: null, s: r.signals ?? 0, p: r.cards ?? 0, at: r.end, e: r.error, route: '/schedule' } : null
}
/** When the next scheduled round starts (``next_round_at``), or that the schedule is off. */
export function nextLine(sched) {
  const next = sched?.next_round_at
  if (!next) return t('home_nextOff')
  return Date.parse(next) <= Date.parse(sched.read_at || next) ? t('home_nextDue') : t('home_next', { at: at(next) })
}

const count = (signals, prefix) => signals.filter((s) => String(s.source).startsWith(prefix)).length
/** The setup checklist on live state. ``ok`` null is a choice, not a step (trust). ``fix`` is the tab that fixes it, or ``refresh``. */
export function checklist({ settings, sched, job, disp, round, signals = [], readAt }) {
  const conf = sched?.schedule
  const gh = count(signals, 'github:'), chats = count(signals, 'session:')
  const chatErr = (round?.notes || []).find((n) => n.startsWith('sessions:'))
  const ghOff = Array.isArray(settings?.repos) && !settings.repos.length  // the owner cleared the repo list
  return [
    { key: 'slack', ok: !!settings?.command_set, route: '/settings', when: readAt, fix: 'settings',
      text: settings?.command_set ? t('slackOn', { n: settings.channels.length, days: settings.window_days }) : t('home_slackNo') },
    ghOff ? { key: 'github', ok: false, route: '/settings', when: readAt, fix: 'settings', text: t('srcA_homeGhOff') }
      : { key: 'github', ok: !!(job?.finished_at && !job.error) || gh > 0, route: '/refresh/status', when: job?.finished_at || readAt, fix: 'refresh',
        text: job?.error ? t('ghFailed', { at: at(job.finished_at), e: job.error }) : job?.finished_at ? t('ghDone', { n: job.rows, at: at(job.finished_at) })
          : gh ? t('home_ghRows', { n: gh }) : t('home_ghNo') },
    { key: 'chats', ok: chats > 0 && !chatErr, route: '/signals', when: readAt, fix: null,
      text: chatErr ? t('home_chatsErr', { e: chatErr }) : chats ? t('home_chatsRows', { n: chats }) : t('home_chatsNo') },
    { key: 'schedule', ok: !!(conf?.round_enabled && conf?.kirocrew_dir), route: '/schedule', when: sched?.read_at, fix: 'settings',
      text: !conf ? t('home_unread') : !conf.round_enabled ? t('home_schedOff') : !conf.kirocrew_dir ? t('home_schedNoDir') : nextLine(sched) },
    { key: 'trust', ok: null, route: '/dispatch', when: readAt, fix: 'settings', on: !!disp?.trust_dispatched,
      text: !disp ? t('home_unread') : disp.trust_dispatched ? t('dispTrustRisk') : t('home_trustOff') },
  ]
}
const stepBadge = (x) => (x.ok === null ? h(UI.Badge, { variant: x.on ? 'warn' : 'muted' }, t(x.on ? 'home_on' : 'home_off'))
  : h(UI.Badge, { variant: x.ok ? 'ok' : 'warn' }, t(x.ok ? 'home_stepDone' : 'home_stepOpen')))

/** A plain link-styled button that moves to another tab. */
const TabLink = ({ to, label, go, testId }) => h('button', { type: 'button', 'data-testid': testId, 'data-to': to, onClick: () => go(to),
  className: 'text-accent hover:underline text-[13px] bg-transparent border-0 p-0 cursor-pointer' }, label)

/** Raw A/B numbers of one prompt change, with where they come from and when the change was proposed. */
function RawAb({ change: c }) {
  const cell = 'px-2 py-1 border-b border-border'
  return h(UI.Card, { 'data-testid': 'home-raw-ab' }, h('div', { className: LABEL }, t('home_rawAb', { agent: c.agent })),
    h('div', { className: 'overflow-x-auto' }, h('table', { className: 'w-full border-collapse text-[13px]' },
      h('thead', null, h('tr', null, ['colMetric', 'colA', 'colB', 'colVerdict'].map((k) => h('th', { key: k, scope: 'col', className: `${cell} text-left text-muted` }, t(k))))),
      h('tbody', null, Object.entries(c.ab.metrics).map(([m, v]) => h('tr', { key: m }, h('td', { className: cell }, h('code', null, m)),
        h('td', { className: cell }, String(v.A ?? '—')), h('td', { className: cell }, String(v.B ?? '—')), h('td', { className: cell }, t(`verdict_${v.verdict}`))))))),
    h('div', { className: MUTED, 'data-testid': 'ab-source' }, t('home_abFrom', { rounds: c.ab.rounds.join(', '), reps: c.ab.reps }), ' ', h('code', null, 'crew/ab.py'),
      ' · ', t('home_proposedAt', { at: at(c.at) }), ' ', h(Src, { route: '/prompt-changes' })))
}

/** The Home tab body. ``readAt`` is when the page last read the routes that carry no time of their own. */
export function Home({ round, sched, settings, job, disp, signals = [], changes = [], readAt, go, armed, setArmed, runRound, refresh, regress, team, scoreLine }) {
  const conf = sched?.schedule
  const list = checklist({ settings, sched, job, disp, round, signals, readAt })
  const steps = list.filter((x) => x.ok !== null)
  const last = lastRound(round, sched?.runs)
  const ab = (changes || []).filter((c) => c.ab)
  return stack(
    h(UI.Card, { 'data-testid': 'home-status' }, h('div', { className: LABEL }, t('home_statusTitle')),
      h('div', { className: 'text-[15px] text-text-strong', 'data-testid': 'home-running' },
        round?.running ? t('home_running', { n: round.round, at: at(round.started_at) }) : t('home_idle'), ' ', h(Src, { route: '/round/status', when: round?.started_at || readAt })),
      h('div', { 'data-testid': 'home-last' }, !last ? t('home_lastNever')
        : last.e ? t('home_lastFailed', { at: at(last.at), e: last.e }) : t('home_last', { s: last.s, p: last.p, at: at(last.at) }),
        ' ', last ? h(Src, { route: last.route, when: last.at }) : null),
      h('div', { 'data-testid': 'home-next' }, conf ? nextLine(sched) : t('home_unread'), ' ', h(Src, { route: '/schedule', when: sched?.read_at })),
      conf ? row(...['round_enabled', 'regress_enabled', 'score_enabled'].map((k) => h(UI.Badge, { key: k, 'data-testid': 'home-switch', 'data-on': !!conf[k],
        variant: conf[k] ? 'ok' : 'muted' }, t(`home_sw_${k}`, { on: t(conf[k] ? 'home_on' : 'home_off') })))) : null),
    h(UI.Card, { 'data-testid': 'home-setup' }, h('div', { className: LABEL }, t('home_setupTitle', { done: steps.filter((x) => x.ok).length, n: steps.length })),
      h('ul', { className: 'list-none m-0 p-0' }, list.map((x) => h('li', { key: x.key, 'data-testid': 'setup-step', 'data-key': x.key, 'data-ok': String(x.ok), className: 'py-1.5' },
        row(stepBadge(x), h('span', { className: 'text-text-strong' }, t(`home_step_${x.key}`)), h(Src, { route: x.route, when: x.when }),
          x.fix === 'refresh' && x.ok !== true ? h(UI.Btn, { type: 'button', onClick: refresh, 'data-testid': 'setup-refresh' }, t('refresh'))
            : x.fix && x.ok !== true ? h(TabLink, { to: x.fix, label: t('home_fixSettings'), go, testId: 'setup-fix' }) : null),
        h('div', { className: MUTED }, x.text))))),
    h(UI.Card, { 'data-testid': 'home-act' }, h('div', { className: LABEL }, t('home_actTitle')),
      row(h(RunRound, { primary: true, armed, running: !!round?.running, onArm: () => setArmed(true), onConfirm: runRound, onCancel: () => setArmed(false) })),
      h('div', { className: MUTED, 'data-testid': 'round-does' }, t('home_roundDoes')),
      h('div', { className: MUTED, 'data-testid': 'round-cost' }, roundCost(sched?.round_stats), ' ',
        h(Src, { route: '/schedule', when: sched?.round_stats?.to || sched?.read_at }))),
    h('details', { 'data-testid': 'home-details', className: 'rounded-md border border-border px-3 py-2' },
      h('summary', { className: MUTED }, t('home_detailsTitle')),
      stack(
        h('div', { className: MUTED }, t('home_detailsSignals'), ' ', h(Src, { route: '/signals', when: readAt })),
        row(h(UI.Btn, { type: 'button', onClick: refresh, 'data-testid': 'details-refresh' }, t('refresh')),
          h('span', { className: MUTED, 'data-testid': 'github-job' }, jobText(job)), h(Src, { route: '/refresh/status' })),
        settings && !settings.command_set ? h('div', { className: MUTED, 'data-testid': 'slack-off' }, slackNote(null)) : null,
        h(Signals, { signals }),
        h(UI.Card, { 'data-testid': 'details-jobs' }, h('div', { className: LABEL }, t('jobsTitle')),
          h('div', { className: MUTED, 'data-testid': 'round-job' }, roundText(round) || t('roundNever')),
          h('div', { className: MUTED, 'data-testid': 'regress' }, regressText(regress)),
          scoreLine ? h('div', { className: MUTED }, scoreLine) : null),
        h(UI.Card, null, h(Runs, { runs: sched?.runs || [] }), h(Src, { route: '/schedule', when: sched?.read_at })),
        ...(ab.length ? ab.map((c) => h(RawAb, { key: c.id, change: c })) : [h('div', { key: 'no-ab', className: MUTED, 'data-testid': 'home-no-ab' }, t('home_noAb'))]),
        h('div', { key: 'team', 'data-testid': 'details-team' }, h('div', { className: LABEL }, t('tab_team')), h(Team, { team }), h(Src, { route: '/team' })))))
}
