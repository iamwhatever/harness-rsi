// Fake `@kirocrew/app-sdk`: the api and the host locale come from globals the check sets.
export const useAppApi = () => globalThis.__api ?? null
export const activeLocale = () => globalThis.__locale || 'en'
