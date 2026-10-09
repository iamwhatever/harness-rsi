// The web side: Home's catch-up report card and the Settings section for topics, trusted sites and X
// accounts (backend/report.py, backend/topics.py). Words come from strings.mjs (the srcB_* block).
import { createElement as h, useCallback, useEffect, useState } from 'react'
import * as UI from '@kirocrew/app-sdk/ui'
import { t } from './strings.mjs'
import { Src, at } from './home.mjs'

const LABEL = 'text-[11px] uppercase tracking-wide text-muted font-semibold mb-1'
const MUTED = 'text-[13px] text-muted'
const LINK = 'text-accent hover:underline break-all'
const list = (x, re) => String(x || '').split(re).map((s) => s.trim()).filter(Boolean)
/** Form strings -> the POST /topics body. Topics may hold spaces, so they split on commas and new lines only. */
export const parseTopics = (f) => ({ topics: list(f.topics, /[,\n]+/), sites: list(f.sites, /[\s,]+/),
  x_handles: list(f.x_handles, /[\s,]+/).map((x) => x.replace(/^@/, '')) })
export const topicsForm = (c) => ({ topics: c.topics.join(', '), sites: c.sites.join(', '), x_handles: c.x_handles.map((x) => `@${x}`).join(', ') })

const FIELDS = ['topics', 'sites', 'x_handles']
/** Settings: what the web side looks for. Stateless, so the page owns the values. */
export function TopicsForm({ form, onChange, onSave, note }) {
  return h(UI.SettingsSection, { title: t('srcB_topicsTitle') },
    h('div', { className: MUTED, 'data-testid': 'topics-help' }, t('srcB_topicsHelp')),
    ...FIELDS.flatMap((k) => [h(UI.SettingsInput, { key: k, name: k, label: t(`srcB_field_${k}`), value: form[k] ?? '', onChange: (v) => onChange({ ...form, [k]: v }) }),
      h('div', { key: `${k}-help`, className: `${MUTED} -mt-1` }, t(`srcB_help_${k}`))]),
    h('div', { className: MUTED, 'data-testid': 'topics-no-login' }, t('srcB_noLogin')),
    h('div', { className: 'flex flex-wrap items-center gap-3 mt-2' },
      h(UI.Btn, { type: 'button', primary: true, onClick: onSave, 'data-testid': 'topics-save' }, t('save')),
      h('span', { className: MUTED, 'aria-live': 'polite' }, note)))
}

const ext = (url, label) => h('a', { href: url, target: '_blank', rel: 'noreferrer', className: LINK }, label)
const items = (rows, testId, line) => h('ul', { className: 'list-none m-0 p-0 flex flex-col gap-1' },
  rows.map((i) => h('li', { key: i.id || i, 'data-testid': testId }, line(i))))
/** What one top trend may mean for us: the card that picks it up, or none yet. */
export const meaning = (i) => (i.card ? t('srcB_meansCard', { id: i.card.id, pain: i.card.pain }) : t('srcB_meansNone'))
/** The one line under the report title: when, by what, from how many signals. */
export const madeLine = (r) => t(r.how === 'round' ? 'srcB_madeRound' : 'srcB_madeButton', { at: at(r.made_at), n: r.round ?? '' })
/** One line for the Make catch-up report job, or ''. */
export const reportJobText = (j) => (!j ? '' : j.running ? t('srcB_making', { at: at(j.started_at) })
  : j.finished_at && j.error ? t('srcB_madeNote', { e: j.error }) : '')

/** Home: the latest catch-up report (GET /report), its Make button and a Slack draft to copy (never posted). */
export function ReportCard({ view, onMake, onCopy, copied, makeNote }) {
  const r = view?.report, job = view?.job
  const sub = (key) => h('div', { className: `${LABEL} mt-2` }, t(key))
  return h(UI.Card, { 'data-testid': 'home-report' }, h('div', { className: LABEL }, t('srcB_reportTitle')),
    h('div', { className: 'flex flex-wrap items-center gap-2' },
      h(UI.Btn, { type: 'button', onClick: onMake, disabled: !!job?.running, 'data-testid': 'report-make' }, t(job?.running ? 'srcB_makeBusy' : 'srcB_make')),
      h('span', { className: MUTED, 'aria-live': 'polite', 'data-testid': 'report-job' }, makeNote || reportJobText(job))),
    h('div', { className: MUTED }, t('srcB_makeDoes')),
    !r ? h('div', { className: MUTED, 'data-testid': 'report-none' }, view === undefined ? t('home_unread') : t('srcB_reportNone'))
      : h('div', { className: 'flex flex-col gap-1 mt-2' },
        h('div', { className: 'text-[15px] text-text-strong', 'data-testid': 'report-date' }, t('srcB_reportFor', { date: r.date })),
        h('div', { className: MUTED, 'data-testid': 'report-made' }, madeLine(r), ' ', h(Src, { route: '/report', when: r.made_at })),
        h('div', { className: MUTED }, t('srcB_topicsLine', { topics: r.topics.join(', ') }), ' ',
          t('srcB_counts', { n: r.counts.external, web: r.counts.web, repos: r.counts.repos })),
        sub('srcB_top'),
        r.trends.length ? h('ol', { className: 'm-0 pl-5 flex flex-col gap-1' }, r.trends.map((i) => h('li', { key: i.id, 'data-testid': 'report-trend', 'data-kind': i.kind },
          ext(i.link, i.pain), h('span', { className: MUTED }, ' · ', t('srcB_heat', { people: i.people, count: i.count })),
          h('div', { className: MUTED, 'data-testid': 'report-means' }, meaning(i)))))
          : h('div', { className: MUTED }, t('srcB_topNone')),
        sub('srcB_repos'),
        r.repos.length || r.releases.length ? items([...r.repos, ...r.releases], 'report-repo', (i) => ext(i.link, i.pain))
          : h('div', { className: MUTED }, t('srcB_reposNone')),
        r.not_reachable.length ? h('div', null, sub('srcB_unreached'), h('div', { className: MUTED }, t('srcB_unreachedHelp')),
          items(r.not_reachable, 'report-unreached', (u) => ext(u, u))) : null,
        h('details', { 'data-testid': 'report-slack', className: 'mt-2' },
          h('summary', { className: MUTED }, t('srcB_slackTitle')),
          h('div', { className: MUTED, role: 'note' }, t('srcB_slackNote')),
          h('pre', { className: 'whitespace-pre-wrap text-[12.5px] bg-bg-elevated rounded-md p-2 mt-1', 'data-testid': 'report-slack-text' }, r.slack),
          h('div', { className: 'flex flex-wrap items-center gap-2' },
            h(UI.Btn, { type: 'button', onClick: () => onCopy(r.slack), 'data-testid': 'report-copy' }, t('srcB_copy')),
            h('span', { className: MUTED, 'aria-live': 'polite' }, copied || '')))))
}

/** The page's web-side state over one data source: the report (polled while it is being made) and the topics form. */
export function useSrcB(src) {
  const [view, setView] = useState(undefined)
  const [[form, note], setForm] = useState([null, ''])
  const [copied, setCopied] = useState('')
  const [makeNote, setMakeNote] = useState('')
  const read = useCallback(() => src.report().then(setView, () => setView(null)), [src])
  useEffect(() => { read() }, [read])
  useEffect(() => { src.topics().then((c) => setForm(c?.topics ? [topicsForm(c), ''] : [null, '']), (e) => setForm([null, String(e?.message || e)])) }, [src])
  useEffect(() => {
    if (!view?.job?.running) return undefined
    const id = setTimeout(read, 5000)
    return () => clearTimeout(id)
  }, [view, read])
  const make = () => { setMakeNote(''); src.makeReport().then((v) => setView((old) => ({ report: old?.report || null, ...v })), (e) => setMakeNote(t('srcB_makeFailed', { e: String(e?.message || e) }))) }
  const copy = (text) => Promise.resolve(globalThis.navigator?.clipboard?.writeText(text))
    .then(() => setCopied(t('srcB_copied')), () => setCopied(t('srcB_copyFailed')))
  const save = () => src.saveTopics(parseTopics(form)).then((c) => setForm([topicsForm(c), t('saved')]), (e) => setForm([form, t('notSaved', { e: String(e?.message || e) })]))
  return {
    card: h(ReportCard, { key: 'report', view, onMake: make, onCopy: copy, copied, makeNote }),
    settings: form ? h(TopicsForm, { key: 't', form, note, onSave: save, onChange: (f) => setForm([f, '']) })
      : note ? h('div', { key: 't', className: MUTED }, note) : null,
  }
}
