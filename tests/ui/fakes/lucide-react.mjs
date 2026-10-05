// Fake `lucide-react`: icons draw nothing. It mirrors the host's stub
// (KiroCrew website/public/vendor/lucide-react.mjs on main): the named exports are
// exactly the host's list below, and every other icon is reachable only through the default export.
const icon = () => null
export default new Proxy({}, { get: () => icon })
export const {
  AlertTriangle, ArrowLeft, ArrowRight, ArrowUp, Bell, Bot, Brain, Building2,
  Calendar, Check, ChevronRight, Clock, Code, Download, ExternalLink,
  Gamepad2, Heart, Home, Loader2, Menu, MessageSquare, Moon, Package,
  Plug, Plus, Power, RefreshCw, Rocket, Search, Settings, Shield, Sparkles,
  Star, Sun, Tag, Trash2, Users, Wand2, Waves, X, Zap,
} = new Proxy({}, { get: () => icon })
