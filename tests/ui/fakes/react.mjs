// Fake `react` for the node checks: elements are plain objects, and a tiny hook runtime lets a whole
// page render, run its effects, settle its promises and render again (__settle).
export const createElement = (type, props, ...c) => ({ type, key: props?.key,
  props: { ...(props || {}), children: c.length === 1 ? c[0] : c } })

let slots = []
let at = 0
let effects = []
let dirty = false
const same = (a, b) => Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((x, i) => Object.is(x, b[i]))
export function useState(v) {
  const k = at++
  if (!(k in slots)) slots[k] = v
  return [slots[k], (n) => { slots[k] = typeof n === 'function' ? n(slots[k]) : n; dirty = true }]
}
export function useEffect(f, deps) {
  const k = at++
  if (!deps || !same(slots[k], deps)) { slots[k] = deps; effects.push(f) }
}
export function useMemo(f, deps) {
  const k = at++
  if (!slots[k] || !same(slots[k].deps, deps)) slots[k] = { v: f(), deps }
  return slots[k].v
}
export const useCallback = (f, deps) => useMemo(() => f, deps)

/** Render until state stops changing; `fresh` drops all hook state first. Returns the last tree. */
export async function __settle(render, fresh = false) {
  if (fresh) slots = []
  for (let i = 0; i < 50; i++) {
    at = 0
    dirty = false
    effects = []
    const tree = render()
    const run = effects
    run.forEach((f) => f())
    await new Promise((r) => setTimeout(r, 0))
    if (!dirty && !run.length) return tree
  }
  throw new Error('the page did not settle')
}
