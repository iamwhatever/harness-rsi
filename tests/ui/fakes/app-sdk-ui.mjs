// Fake `@kirocrew/app-sdk/ui`: each host component becomes plain elements, and every string prop it
// would show (title, label, subtitle, message) becomes text, so the string-table check can see it.
import { createElement as h } from 'react'

// A primary button keeps a data-primary mark, so a check can count the primary actions on a screen.
const pass = (tag) => ({ children, primary, variant, ...rest }) => h(tag, primary ? { ...rest, 'data-primary': true } : rest, children)
export const Card = pass('div'), Btn = pass('button'), Badge = pass('span'), Input = pass('input')
export const PageHeader = ({ title, subtitle }) => h('header', null, title, subtitle)
export const EmptyState = ({ title, subtitle, testId = 'empty-state' }) => h('div', { 'data-testid': testId }, title, subtitle)
export const StatCard = ({ label, value }) => h('div', { 'data-testid': 'stat' }, label, String(value))
export const ContentSkeleton = () => h('div', { 'data-testid': 'skeleton' })
export const ErrorNotice = ({ title, message, testId }) => h('div', { role: 'alert', 'data-testid': testId }, title, message)
export const SegmentedControl = ({ segments, value, onChange, ariaLabel }) => h('div', { role: 'radiogroup', 'aria-label': ariaLabel },
  segments.map((s) => h('button', { key: s.key, role: 'radio', 'aria-checked': s.key === value, 'data-tab': s.key, onClick: () => onChange(s.key) }, s.label)))
export const SettingsSection = ({ title, children }) => h('section', null, h('h2', null, title), children)
export const SettingsInput = ({ label, ...rest }) => h('label', null, label, h('input', rest))
export const SettingsToggle = ({ label, ...rest }) => h('label', null, label, h('input', { type: 'checkbox', ...rest }))
export const SettingsSelect = ({ label, options, optionLabels, ...rest }) => h('label', null, label,
  h('select', rest, options.map((o, i) => h('option', { key: o, value: o }, optionLabels[i]))))
