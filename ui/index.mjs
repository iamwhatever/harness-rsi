// Harness RSI priority board. Hand-written ESM, no build step: the host's import
// map resolves `react` and `@kirocrew/app-sdk/ui`, as it does for a built bundle.
import { createElement as h, useCallback, useEffect, useMemo, useState } from 'react'
import { useAppApi } from '@kirocrew/app-sdk'
import { Badge, Btn, Card, EmptyState, PageHeader } from '@kirocrew/app-sdk/ui'

const BASE = '/api/apps/harness-rsi'

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
    round: async () => (await api.get(`${BASE}/round/status`)).round,
    runRound: async () => (await api.post(`${BASE}/round/run`, {})).round,
    regress: async () => (await api.get(`${BASE}/regress`)).runs[0] || null,
    // The backend reads Slack itself; when Slack is off or fails, errors say why.
    refresh: async () => { const r = await api.post(`${BASE}/refresh`, {}); return { ...r, errors: r.errors || [] } },
    settings: async () => (await api.get(`${BASE}/settings`)).settings,
    saveSettings: async (form) => (await api.post(`${BASE}/settings`, parseSettings(form))).settings,
  }
}

const words = (t) => String(t || '').split(/[\s,]+/).filter(Boolean)
/** Form strings -> the POST /settings body; a blank command keeps the saved one (and its args). */
export const parseSettings = (f) => {
  const command = String(f.command || '').trim()
  return { ...(command ? { command, args: words(f.args) } : {}), channels: words(f.channels),
    window_days: Number(f.window_days), workspace_url: String(f.workspace_url || '').trim() }
}
/** The backend shows only whether a command is set, so its inputs start blank. */
export const toForm = (s) => ({ saved: s, command: '', args: '', channels: s.channels.join(', '),
  window_days: String(s.window_days), workspace_url: s.workspace_url })
export const slackNote = (s) => (s?.command_set ? `Slack: on, reading ${s.channels.length} channel(s) over ${s.window_days} days`
  : 'Slack collection is off: set the Slack MCP command to turn it on.')
const FIELDS = [['command', 'Slack MCP command (blank keeps the saved one)'], ['args', 'Arguments'], ['channels', 'Channel ids'],
  ['window_days', 'Window (days)'], ['workspace_url', 'Workspace URL (for links)']]
/** The Slack settings form: stateless, so the page owns the values. */
export function SettingsForm({ form, onChange, onSave, note }) {
  const input = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--panel)',
    color: 'var(--text)', fontSize: 13, width: '100%' }
  return h(Card, { 'data-testid': 'settings' },
    h('div', { 'data-testid': 'slack-configured' }, `Slack MCP command configured: ${form.saved.command_set ? 'yes' : 'no'}`),
    h('div', { style: muted, 'data-testid': 'slack-note' }, slackNote(form.saved)),
    FIELDS.map(([k, text]) => h('label', { key: k, style: { display: 'block', marginTop: 10, ...muted } }, text,
      h('input', { name: k, value: form[k] ?? '', style: input, onChange: (e) => onChange({ ...form, [k]: e.target.value }) }))),
    h('div', { style: row({ alignItems: 'center' }) }, h(Btn, { type: 'button', primary: true, onClick: onSave }, 'Save'),
      h('span', { style: muted, 'aria-live': 'polite' }, note)))
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

const TABS = [['board', 'Board'], ['signals', 'Signals'], ['settings', 'Settings']]
const why = (e) => String(e?.message || e)
const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
/** One line for the GitHub job (it runs for minutes after a refresh). */
export const jobText = (j) => (!j ? '' : j.running ? `GitHub: fetching since ${clock(j.started_at)}…`
  : !j.finished_at ? 'GitHub: not fetched yet' : j.error ? `GitHub: failed at ${clock(j.finished_at)} (${j.error})`
    : `GitHub: ${j.rows} rows at ${clock(j.finished_at)}`)
/** One line for the latest post-merge regression run. */
export const regressText = (r) => (!r ? 'Regression: no run yet' : `Regression @ ${r.sha.slice(0, 7)}: `
  + `${r.counts.pass}/${r.counts.run} pass · ${r.regressions.length ? `${r.regressions.length} regression(s)` : 'no regressions'}`)
/** One line for the design-crew round (it runs for many minutes). */
export const roundText = (r) => (!r ? '' : r.running ? `Round ${r.round}: running since ${clock(r.started_at)}…`
  : !r.finished_at ? 'Round: not run yet' : r.error ? `Round ${r.round}: failed at ${clock(r.finished_at)} (${r.error})`
    : [`Round ${r.round}: ${r.counts.proposals} proposals from ${r.counts.signals} signals at ${clock(r.finished_at)}`,
      ...r.notes].join(' · '))
/** Run round, two steps: the first click arms, the second confirms. */
export function RunRound({ armed, running, onArm, onConfirm, onCancel }) {
  if (running) return h(Btn, { type: 'button', disabled: true }, 'Round running…')
  if (!armed) return h(Btn, { type: 'button', onClick: onArm, 'data-testid': 'round-arm' }, 'Run round')
  return h('span', { style: { display: 'flex', gap: 8 } },
    h(Btn, { type: 'button', primary: true, onClick: onConfirm, 'data-testid': 'round-confirm' }, 'Confirm: run round'),
    h(Btn, { type: 'button', onClick: onCancel }, 'Cancel'))
}
/** The page body. `load` is the data source; `onDecide` saves a choice; `onRefresh` pulls
 *  signals; `onStatus` reads the GitHub job, polled while it runs. */
export function HarnessRsi({ load, onDecide, onRefresh, onStatus, onSettings, onSaveSettings, onRegress, onRoundStatus, onRunRound }) {
  const [regress, setRegress] = useState(undefined)
  useEffect(() => { onRegress?.().then(setRegress, () => {}) }, [onRegress])
  const [[data, error], setState] = useState([null, ''])
  const [tab, setTab] = useState('board')
  const [note, setNote] = useState('')
  const reload = useCallback(() => load().then((d) => setState([d, '']), (e) => setState([null, why(e)])), [load])
  useEffect(() => { reload() }, [reload])
  const [job, setJob] = useState(null)
  const [[form, formNote], setForm] = useState([null, ''])
  useEffect(() => { onSettings?.().then((v) => setForm([toForm(v), '']), (e) => setForm([null, why(e)])) }, [onSettings])
  const save = () => onSaveSettings(form).then((v) => setForm([toForm(v), 'Saved']), (e) => setForm([form, `Not saved: ${why(e)}`]))
  useEffect(() => { onStatus?.().then(setJob, () => {}) }, [onStatus])
  useEffect(() => {
    if (!job?.running || !onStatus) return undefined
    const t = setTimeout(() => onStatus().then((j) => { setJob(j); if (!j.running) reload() }, () => setJob({ ...job })), 5000)
    return () => clearTimeout(t)
  }, [job, onStatus, reload])
  const [[round, armed], setRound] = useState([null, false])
  useEffect(() => { onRoundStatus?.().then((r) => setRound([r, false]), () => {}) }, [onRoundStatus])
  useEffect(() => {
    if (!round?.running || !onRoundStatus) return undefined
    const t = setTimeout(() => onRoundStatus().then((r) => { setRound([r, false]); if (!r.running) reload() },
      () => setRound([{ ...round }, false])), 5000)
    return () => clearTimeout(t)
  }, [round, onRoundStatus, reload])
  const runRound = () => { setRound([round, false]); onRunRound().then((r) => setRound([r, false]), (e) => setNote(`Could not start the round: ${why(e)}`)) }
  // The card shows the choice at once; the reload then shows what decisions.jsonl holds.
  const decide = (id, decision) => {
    setState(([d]) => [{ ...d, proposals: applyDecision(d.proposals, id, decision) }, ''])
    const said = DECISIONS.find((x) => x[0] === decision)[1]
    Promise.resolve(onDecide?.(id, decision)).then(() => { setNote(`Saved: ${said}`); reload() },
      (e) => { setNote(`Could not save the decision: ${why(e)}`); reload() })
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
      : tab === 'settings' ? (form ? h(SettingsForm, { form, note: formNote, onSave: save,
        onChange: (f) => setForm([f, ''])}) : h('div', { style: muted }, formNote || 'Loading…'))
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
        h('span', { style: { marginLeft: 'auto', display: 'flex', gap: 8 } },
          onRunRound ? h(RunRound, { armed, running: round?.running, onArm: () => setRound([round, true]),
            onConfirm: runRound, onCancel: () => setRound([round, false]) }) : null,
          onRefresh ? h(Btn, { type: 'button', onClick: refresh }, 'Refresh signals') : null)),
      h('div', { style: muted, 'aria-live': 'polite', 'data-testid': 'note' }, note),
      h('div', { style: muted, 'aria-live': 'polite', 'data-testid': 'github-job' }, jobText(job)),
      h('div', { style: muted, 'aria-live': 'polite', 'data-testid': 'round-job' }, roundText(round)),
      regress !== undefined ? h('div', { style: muted, 'data-testid': 'regress' }, regressText(regress)) : null,
      form && !form.saved.command_set ? h('div', { style: muted, 'data-testid': 'slack-off' }, slackNote(null)) : null,
      h('div', { role: 'tabpanel', id: 'rsi-panel', 'aria-labelledby': `tab-${tab}` }, body)))
}

/** The installed page: the backend is the data source. */
export default function HarnessRsiPage() {
  const api = useAppApi()
  const src = useMemo(() => backendSource(api), [api])
  return h(HarnessRsi, { load: src.load, onDecide: src.decide, onRefresh: src.refresh, onStatus: src.status,
    onSettings: src.settings, onSaveSettings: src.saveSettings, onRegress: src.regress,
    onRoundStatus: src.round, onRunRound: src.runRound })
}
