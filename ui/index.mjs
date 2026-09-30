// Harness RSI priority board. Hand-written ESM, no build step: the host's import
// map resolves `react` and `@kirocrew/app-sdk/ui`, as it does for a built bundle.
import { createElement as h, useCallback, useEffect, useMemo, useState } from 'react'
import { useAppApi } from '@kirocrew/app-sdk'
import { Badge, Btn, Card, EmptyState, PageHeader } from '@kirocrew/app-sdk/ui'

const BASE = '/api/apps/harness-rsi'
const SLACK_EXPORT = '/api/apps/slack-radar/signals'

/** The data source over the app backend. `load` answers { proposals, signals, images }
 *  (images[id].before is a screenshot URL of the page today); tests pass fake-data.mjs instead. */
export function backendSource(api) {
  return {
    load: async () => {
      const [p, s] = await Promise.all([api.get(`${BASE}/proposals`), api.get(`${BASE}/signals`)])
      return { proposals: p.proposals, signals: s.signals, images: {} }
    },
    decide: (id, decision) => api.post(`${BASE}/decisions`, { proposal_id: id, decision }),
    status: async () => (await api.get(`${BASE}/refresh/status`)).github,
    // Slack Radar may be off or missing: GitHub still refreshes, and the reason is shown.
    refresh: async () => {
      let slack = [], errors = []
      try { slack = (await api.get(SLACK_EXPORT)).signals || [] } catch { errors = ['slack: Slack Radar export not reachable'] }
      const r = await api.post(`${BASE}/refresh`, { slack })
      return { ...r, errors: [...errors, ...(r.errors || [])] }
    },
  }
}

export const DECISIONS = [['do', '做', 'Doing'], ['skip', '不做', 'Not doing'], ['later', '以后再说', 'Later']]

/** Most people first, then the shortest window. */
export const byHeat = (list) =>
  [...list].sort((a, b) => b.heat.people - a.heat.people || a.heat.window_days - b.heat.window_days)

/** Hottest first; a merged duplicate sinks below every primary signal. */
export const signalsByHeat = (list) =>
  [...list].sort((a, b) => !!a.dedup_of - !!b.dedup_of
    || b.mentions.people - a.mentions.people || b.mentions.count - a.mentions.count)

export const applyDecision = (list, id, decision) => list.map((p) => (p.id === id ? { ...p, decision } : p))

// Host --muted is 4.3:1 on a card; mixing in --text lifts it past 4.5:1 in every theme.
const MUTED = 'color-mix(in srgb, var(--muted) 70%, var(--text))'
const muted = { color: MUTED, fontSize: 13 }
const label = { ...muted, fontSize: 12, textTransform: 'uppercase', letterSpacing: 0.4, margin: '0 0 4px' }
const row = (extra) => ({ display: 'flex', gap: 12, marginTop: 12, ...extra })
const frameBox = { height: 150, borderRadius: 8, border: '1px solid var(--border)', background: 'var(--panel)',
  display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }

const Frame = ({ title, children }) => h('figure', { style: { margin: 0, flex: 1, minWidth: 0 } },
  h('figcaption', { style: label }, title), h('div', { style: frameBox }, children))
const Section = ({ title, children }) => h('div', null, h('div', { style: label }, title), children)

export function ProposalCard({ proposal: p, before, onDecide }) {
  const titleId = `pain-${p.id}`
  const slug = p.mock_artifact_slug
  return h(Card, { role: 'region', 'aria-labelledby': titleId, 'data-testid': 'proposal-card', 'data-id': p.id },
    h('div', { style: { display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' } },
      h('h3', { id: titleId, style: { margin: 0, fontSize: 16, fontWeight: 600, color: 'var(--text-strong)' } }, p.pain),
      h(Badge, { variant: 'warn', 'data-testid': 'heat' }, `${p.heat.people} people / ${p.heat.window_days} days`)),
    h('div', { style: row() },
      h(Frame, { title: 'Before' }, before
        ? h('img', { src: before, alt: `Current page for: ${p.pain}`,
          style: { width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'top left' } })
        : h('span', { style: muted }, 'No screenshot yet')),
      h(Frame, { title: 'After (mock)' }, slug
        ? h('a', { href: `/artifacts/${slug}`, style: { color: 'var(--accent)', fontSize: 13 } }, `Open mock: ${slug}`)
        : h('span', { style: muted }, 'No mock yet'))),
    h('div', { style: row({ flexWrap: 'wrap', gap: 32 }) },
      h(Section, { title: 'Cost' },
        h('div', { 'data-testid': 'cost' }, `${p.cost.files} files · ${p.cost.lines} lines`),
        h('div', { style: muted }, p.cost.risks.length ? `Risks: ${p.cost.risks.join(', ')}` : 'No known risks')),
      h(Section, { title: 'Exams' }, h('div', { style: { display: 'flex', gap: 6, flexWrap: 'wrap' } },
        p.exam_ids.map((e) => h(Badge, { key: e, variant: 'muted' }, e))))),
    h('div', { role: 'group', 'aria-label': `Decision for: ${p.pain}`, style: row({ gap: 8, alignItems: 'center' }) },
      DECISIONS.map(([value, text]) => h(Btn, { key: value, type: 'button', 'data-decision': value,
        primary: p.decision === value, 'aria-pressed': p.decision === value, onClick: () => onDecide(p.id, value) }, text)),
      h('span', { style: muted, 'aria-live': 'polite' },
        p.decision ? `Decided: ${DECISIONS.find((d) => d[0] === p.decision)[2]}` : 'Not decided')))
}

export function Board({ proposals, images = {}, onDecide }) {
  if (!proposals.length) return h(EmptyState, { icon: null, title: 'No proposals yet' })
  return h('div', { style: { display: 'flex', flexDirection: 'column', gap: 12 } }, byHeat(proposals).map((p) =>
    h(ProposalCard, { key: p.id, proposal: p, before: images[p.id]?.before, onDecide })))
}

const COLS = ['Pain', 'People', 'Mentions', 'Days', 'Source', 'Layer']
export function Signals({ signals }) {
  if (!signals.length) return h(EmptyState, { icon: null, title: 'No signals yet' })
  const cell = { padding: '8px 10px', borderBottom: '1px solid var(--border)', textAlign: 'left', fontSize: 13 }
  const td = (...c) => h('td', { style: cell }, ...c)
  return h(Card, null, h('table', { style: { width: '100%', borderCollapse: 'collapse' } },
    h('caption', { style: { ...label, textAlign: 'left' } }, 'Signals, hottest first'),
    h('thead', null, h('tr', null, COLS.map((c) => h('th', { key: c, scope: 'col', style: { ...cell, ...muted } }, c)))),
    h('tbody', null, signalsByHeat(signals).map((s) => h('tr', { key: s.id, 'data-testid': 'signal-row' },
      td(h('a', { href: s.links[0], target: '_blank', rel: 'noreferrer', style: { color: 'var(--text)' } }, s.pain),
        s.dedup_of ? h('div', { style: muted }, `Merged into ${s.dedup_of}`) : null),
      td(s.mentions.people), td(s.mentions.count), td(s.mentions.window_days),
      td(h('code', null, s.source)), td(h(Badge, { variant: s.layer === 'real' ? 'ok' : 'aim' }, s.layer)))))))
}

const TABS = [['board', 'Board'], ['signals', 'Signals']]
const why = (e) => String(e?.message || e)
const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
/** One line for the GitHub job (it runs for minutes after a refresh). */
export const jobText = (j) => (!j ? '' : j.running ? `GitHub: fetching since ${clock(j.started_at)}…`
  : !j.finished_at ? 'GitHub: not fetched yet' : j.error ? `GitHub: failed at ${clock(j.finished_at)} (${j.error})`
    : `GitHub: ${j.rows} rows at ${clock(j.finished_at)}`)
/** The page body. `load` is the data source; `onDecide` saves a choice; `onRefresh` pulls
 *  signals; `onStatus` reads the GitHub job, polled while it runs. */
export function HarnessRsi({ load, onDecide, onRefresh, onStatus }) {
  const [[data, error], setState] = useState([null, ''])
  const [tab, setTab] = useState('board')
  const [note, setNote] = useState('')
  const reload = useCallback(() => load().then((d) => setState([d, '']), (e) => setState([null, why(e)])), [load])
  useEffect(() => { reload() }, [reload])
  const [job, setJob] = useState(null)
  useEffect(() => { onStatus?.().then(setJob, () => {}) }, [onStatus])
  useEffect(() => {
    if (!job?.running || !onStatus) return undefined
    const t = setTimeout(() => onStatus().then((j) => { setJob(j); if (!j.running) reload() }, () => setJob({ ...job })), 5000)
    return () => clearTimeout(t)
  }, [job, onStatus, reload])
  const decide = (id, decision) => {
    setState(([d]) => [{ ...d, proposals: applyDecision(d.proposals, id, decision) }, ''])
    Promise.resolve(onDecide?.(id, decision)).catch((e) => setNote(`Could not save the decision: ${why(e)}`))
  }
  const refresh = () => {
    setNote('Refreshing signals…')
    onRefresh().then((r) => { setNote([`Signals: ${r.total} (${r.added} new)`, ...r.errors].join(' · ')); setJob(r.github || null); reload() },
      (e) => setNote(`Could not refresh: ${why(e)}`))
  }
  const tabStyle = (on) => ({ padding: '6px 12px', borderRadius: 8, border: 0, cursor: 'pointer', fontSize: 14,
    background: on ? 'var(--bg-hover)' : 'transparent', color: on ? 'var(--text-strong)' : MUTED })
  const body = error ? h('div', { role: 'alert', style: { color: 'var(--danger)' } }, `Could not load data: ${error}`)
    : !data ? h('div', { style: muted }, 'Loading…')
      : tab === 'board' ? h(Board, { proposals: data.proposals, images: data.images, onDecide: decide })
        : h(Signals, { signals: data.signals })
  return h('div', { style: { flex: 1, overflowY: 'auto' } },
    h(PageHeader, { title: 'Harness RSI', subtitle: 'Pick what to build next. Nothing runs until you decide.' }),
    h('div', { style: { padding: '0 24px 24px', display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 960 } },
      h('div', { style: { display: 'flex', gap: 4, alignItems: 'center', flexWrap: 'wrap' } },
        h('div', { role: 'tablist', 'aria-label': 'Harness RSI sections', style: { display: 'flex', gap: 4 } },
          TABS.map(([id, text]) => h('button', { key: id, type: 'button', role: 'tab', id: `tab-${id}`,
            'aria-selected': tab === id, 'aria-controls': 'rsi-panel', onClick: () => setTab(id), style: tabStyle(tab === id) },
          text))),
        onRefresh ? h(Btn, { type: 'button', onClick: refresh, style: { marginLeft: 'auto' } }, 'Refresh signals') : null),
      h('div', { style: muted, 'aria-live': 'polite', 'data-testid': 'note' }, note),
      h('div', { style: muted, 'aria-live': 'polite', 'data-testid': 'github-job' }, jobText(job)),
      h('div', { role: 'tabpanel', id: 'rsi-panel', 'aria-labelledby': `tab-${tab}` }, body)))
}

/** The installed page: the backend is the data source. */
export default function HarnessRsiPage() {
  const api = useAppApi()
  const src = useMemo(() => backendSource(api), [api])
  return h(HarnessRsi, { load: src.load, onDecide: src.decide, onRefresh: src.refresh, onStatus: src.status })
}
