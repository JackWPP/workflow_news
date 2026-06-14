# Light Theme Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the frontend from a dark glassmorphism theme to a clean, academic light theme inspired by the logo's royal blue color (#2b5797).

**Architecture:** CSS variable-driven theme swap in `style.css` as the foundation, then component-by-component cleanup of dark-specific styles (glows, particle canvas, dark backgrounds). No logic changes — purely visual.

**Tech Stack:** Vue 3, Tailwind CSS 4, CSS custom properties, Lucide icons

---

## Color System Reference

| Token | Old (Dark) | New (Light) | Usage |
|-------|-----------|-------------|-------|
| `--bg-primary` | `#0a0e1a` | `#f5f7fa` | Page background |
| `--bg-surface` | `rgba(15,20,40,0.85)` | `#ffffff` | Panels, sidebar |
| `--bg-card` | `rgba(20,28,55,0.7)` | `#ffffff` | Cards |
| `--bg-hover` | `rgba(30,40,70,0.9)` | `#f1f5f9` | Hover states |
| `--border-glow` | `rgba(100,180,255,0.15)` | `#e2e8f0` | Borders |
| `--line` | `rgba(255,255,255,0.1)` | `#e2e8f0` | Dividers |
| `--shadow` | `0 12px 34px rgba(0,0,0,0.3)` | `0 1px 3px rgba(0,0,0,0.08)` | Shadows |
| `--text-primary` | `#e8ecf4` | `#1a1a2e` | Main text |
| `--text-secondary` | `#8892a8` | `#64748b` | Secondary text |
| `--text-muted` | `#5c647a` | `#94a3b8` | Muted text |
| `--accent-academic` | `#6cb4ff` | `#2b5797` | Primary blue (from logo) |
| `--accent-industry` | `#4ade80` | `#16a34a` | Industry green |
| `--accent-policy` | `#a78bfa` | `#7c3aed` | Policy purple |
| `--glow-*` | neon shadows | `none` | Removed |

---

## File Map

| File | Changes | Task |
|------|---------|------|
| `frontend/src/style.css` | CSS variables + all utility classes | Task 1 |
| `frontend/src/components/AppShell.vue` | Sidebar + remove particle canvas | Task 2 |
| `frontend/src/components/HeroSection.vue` | Hero gradient + text colors | Task 3 |
| `frontend/src/components/ReportItemCard.vue` | Card styles + hover | Task 4 |
| `frontend/src/components/SectionDivider.vue` | Divider capsule styles | Task 5 |
| `frontend/src/views/DashboardView.vue` | Button/panel colors | Task 6 |
| `frontend/src/components/CoverageGauge.vue` | Remove glow from bar | Task 7 |
| `frontend/src/components/AgentProgressPanel.vue` | Panel background + steps | Task 8 |
| `frontend/src/components/ChatBubble.vue` | Bubble colors | Task 9 |
| `frontend/src/views/ChatView.vue` | Chat interface colors | Task 10 |
| `frontend/src/views/LoginView.vue` | Login card | Task 11 |
| `frontend/src/views/HistoryView.vue` | History list + detail | Task 12 |
| `frontend/src/views/AgentTraceView.vue` | Trace view colors | Task 13 |
| `frontend/src/views/AdminView.vue` | Admin panel colors | Task 14 |

---

### Task 1: Core CSS Variables & Global Styles

**Files:**
- Modify: `frontend/src/style.css`

- [ ] **Step 1: Replace CSS custom properties**

Open `frontend/src/style.css` and replace the entire `:root` block (lines 8-44) with the new light theme variables:

```css
:root {
  /* Foundations */
  --bg-primary: #f5f7fa;
  --bg-surface: #ffffff;
  --bg-card: #ffffff;
  --bg-hover: #f1f5f9;
  --border-glow: #e2e8f0;
  --line: #e2e8f0;
  --shadow: 0 1px 3px rgba(0, 0, 0, 0.08), 0 1px 2px rgba(0, 0, 0, 0.06);

  /* Text */
  --text-primary: #1a1a2e;
  --text-secondary: #64748b;
  --text-muted: #94a3b8;

  /* Accents */
  --accent-academic: #2b5797;
  --accent-industry: #16a34a;
  --accent-policy: #7c3aed;
  --accent-primary: var(--accent-academic);

  /* Status */
  --status-ok: #16a34a;
  --status-warn: #d97706;
  --status-error: #dc2626;
  --status-info: #2563eb;

  /* Glows — intentionally empty (light theme has no neon glows) */

  /* Typography */
  --font-sans: 'Inter', 'Noto Sans SC', system-ui, -apple-system, sans-serif;
  --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
}
```

- [ ] **Step 2: Update body styles**

In the `body` rule (line 54), change the background and text color references. The CSS variables handle this automatically, but verify the `h1-h6` rule — change `color: #ffffff` to `color: var(--text-primary)`:

```css
h1, h2, h3, h4, h5, h6 {
  margin: 0;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.02em;
}
```

- [ ] **Step 3: Update glass-panel utility**

Replace the `.glass-panel` class (lines 106-113):

```css
.glass-panel {
  background: var(--bg-surface);
  border: 1px solid var(--border-glow);
  border-radius: 16px;
  box-shadow: var(--shadow);
}
```

- [ ] **Step 4: Update glass-card utility**

Replace the `.glass-card` class (lines 115-130):

```css
.glass-card {
  background: var(--bg-card);
  border: 1px solid var(--border-glow);
  border-radius: 12px;
  box-shadow: var(--shadow);
  transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
}

.glass-card:hover {
  background: var(--bg-hover);
  border-color: #cbd5e1;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  transform: translateY(-2px);
}
```

- [ ] **Step 5: Update button variants**

Replace `.btn-primary` (lines 133-147):

```css
.btn-primary {
  background: var(--accent-academic);
  color: #ffffff;
  border: 1px solid transparent;
  padding: 8px 16px;
  border-radius: 8px;
  font-weight: 500;
  transition: all 0.2s ease;
}

.btn-primary:hover {
  background: #1e3f73;
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(43, 87, 151, 0.3);
}
```

Replace `.btn-ghost` (lines 149-159):

```css
.btn-ghost {
  color: var(--text-secondary);
  padding: 8px 16px;
  border-radius: 8px;
  transition: all 0.2s ease;
}

.btn-ghost:hover {
  color: var(--text-primary);
  background: rgba(0, 0, 0, 0.04);
}
```

- [ ] **Step 6: Update form inputs**

Replace the input/textarea base styles (lines 162-176):

```css
input, textarea {
  width: 100%;
  background: #ffffff;
  border: 1px solid var(--border-glow);
  color: var(--text-primary);
  padding: 12px 16px;
  border-radius: 8px;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

input:focus, textarea:focus {
  outline: none;
  border-color: var(--accent-primary);
  box-shadow: 0 0 0 3px rgba(43, 87, 151, 0.1);
}
```

- [ ] **Step 7: Update scrollbar styles**

Replace scrollbar styles (lines 178-192):

```css
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.15);
  border-radius: 4px;
}
::-webkit-scrollbar-thumb:hover {
  background: rgba(0, 0, 0, 0.25);
}
```

- [ ] **Step 8: Update status pills**

Replace status pill styles (lines 194-209):

```css
.status-ok { background: rgba(22, 163, 74, 0.08); color: var(--status-ok); border: 1px solid rgba(22, 163, 74, 0.2); }
.status-warn { background: rgba(217, 119, 6, 0.08); color: var(--status-warn); border: 1px solid rgba(217, 119, 6, 0.2); }
.status-error { background: rgba(220, 38, 38, 0.08); color: var(--status-error); border: 1px solid rgba(220, 38, 38, 0.2); }
.status-info { background: rgba(37, 99, 235, 0.08); color: var(--status-info); border: 1px solid rgba(37, 99, 235, 0.2); }
```

- [ ] **Step 9: Update markdown prose styles**

Replace `.prose` block (lines 234-270):

```css
.prose {
  color: var(--text-primary);
  line-height: 1.7;
}
.prose h1, .prose h2, .prose h3 {
  color: var(--text-primary);
  margin-top: 1.5em;
  margin-bottom: 0.5em;
}
.prose a {
  color: var(--accent-primary);
}
.prose code {
  background: rgba(0, 0, 0, 0.05);
  padding: 0.2em 0.4em;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 0.9em;
  color: var(--accent-policy);
}
.prose pre {
  background: #f8fafc;
  padding: 1em;
  border-radius: 8px;
  border: 1px solid var(--border-glow);
  overflow-x: auto;
}
.prose pre code {
  background: transparent;
  padding: 0;
  color: inherit;
}
.prose blockquote {
  border-left: 3px solid var(--accent-primary);
  margin-left: 0;
  padding-left: 1em;
  color: var(--text-secondary);
}
```

- [ ] **Step 10: Remove glow animation classes**

Remove or neutralize these classes that only made sense in dark theme:
- Remove `.animate-pulse-glow` keyframes and class (lines 212-218)
- Keep `.animate-float` (still useful)

- [ ] **Step 11: Verify dev server renders correctly**

Run: `cd frontend && npm run dev` — open browser and confirm the base theme applies without crashes.

---

### Task 2: AppShell — Sidebar & Remove Particles

**Files:**
- Modify: `frontend/src/components/AppShell.vue`

- [ ] **Step 1: Remove particle canvas and import**

In the `<script setup>` block, remove the particle-related code:

Remove these lines:
```ts
import { ParticleSystem } from '../lib/particles'
```

Remove these lines:
```ts
const bgCanvas = ref<HTMLCanvasElement | null>(null)
let particles: ParticleSystem | null = null
```

Remove the `onMounted` body (keep the hook but empty it or remove entirely):
```ts
onMounted(() => {
  // particles removed for light theme
})
```

Remove the `onUnmounted` body:
```ts
onUnmounted(() => {
  // particles removed for light theme
})
```

- [ ] **Step 2: Remove canvas element from template**

In the template (line 49), remove the canvas element:
```html
<canvas ref="bgCanvas" class="fixed inset-0 pointer-events-none z-0 mix-blend-screen opacity-40"></canvas>
```

- [ ] **Step 3: Update sidebar styles**

Replace the `<style scoped>` block (lines 106-124):

```css
.sidebar {
  box-shadow: 1px 0 0 var(--line);
  background: var(--bg-surface);
}

.active-link {
  color: var(--accent-academic) !important;
  background: rgba(43, 87, 151, 0.08) !important;
  font-weight: 600;
}

.logo-img {
  width: 8rem;
  height: 8rem;
  object-fit: contain;
  border-radius: 1.25rem;
}
```

- [ ] **Step 4: Update sidebar template classes**

In the template, the `aside` element (line 51) — remove `glass-panel` and add explicit white background:
```html
<aside class="sidebar w-64 flex-shrink-0 flex flex-col z-10 border-r border-[var(--line)] h-full relative bg-[var(--bg-surface)]">
```

Update nav link hover class (line 66) — replace dark hover with light hover:
```
:class="[route.path === item.to ? 'active-link' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[rgba(0,0,0,0.03)]']"
```

Update user avatar background (line 76):
```
class="w-8 h-8 rounded-full bg-[rgba(0,0,0,0.05)] flex items-center justify-center"
```

Update logout button (line 86):
```
class="flex items-center justify-center gap-2 w-full py-2.5 text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)] bg-[rgba(0,0,0,0.02)] hover:bg-[rgba(0,0,0,0.05)] rounded-lg transition-colors"
```

- [ ] **Step 5: Verify sidebar renders correctly**

Run dev server, confirm sidebar is white with blue active highlights, no particle animation.

---

### Task 3: HeroSection — Light Gradient

**Files:**
- Modify: `frontend/src/components/HeroSection.vue`

- [ ] **Step 1: Update hero background**

Replace the template hero background area (lines 37-41):

```html
<div class="absolute inset-0 z-0">
  <img v-if="heroItem?.image_url" :src="heroItem.image_url" class="w-full h-full object-cover opacity-10 transition-transform duration-1000 group-hover:scale-105" />
  <div v-else class="w-full h-full bg-gradient-to-br from-blue-50 to-slate-50"></div>
  <div class="absolute inset-0 bg-gradient-to-r from-white via-white/90 to-transparent"></div>
</div>
```

- [ ] **Step 2: Update hero text styles**

Update the tag badge (line 46):
```html
<span class="px-3 py-1 bg-blue-50 rounded-full text-xs font-semibold tracking-widest text-[var(--accent-academic)] border border-blue-100 uppercase">
```

Update the title (line 55) — remove drop-shadow:
```html
<h2 class="text-4xl font-bold text-[var(--text-primary)] mb-4 leading-tight tracking-tight">
```

Update the summary paragraph (line 59):
```html
<p class="text-[var(--text-secondary)] text-lg leading-relaxed line-clamp-3 md:line-clamp-none max-w-xl">
```

Update the thin report note box (line 63):
```html
<p v-if="thinReportNote" class="text-sm text-[var(--text-secondary)] bg-blue-50 border border-blue-100 rounded-xl px-4 py-3">
```

- [ ] **Step 3: Update hero button**

Replace the regenerate button (lines 73-80):
```html
<button 
  @click="$emit('regenerate')" 
  :disabled="loading"
  class="flex items-center gap-2 bg-[var(--accent-academic)] text-white font-bold px-5 py-2.5 rounded-xl transition-all hover:bg-[#1e3f73] disabled:opacity-50 disabled:cursor-not-allowed"
>
```

Update the date/coverage info section (line 82):
```html
<div v-if="report" class="flex items-center gap-6 px-4 py-2 border-l border-[var(--line)] ml-2">
```

- [ ] **Step 4: Verify hero renders**

Confirm hero section shows light blue-gray gradient, dark text, blue button.

---

### Task 4: ReportItemCard — Clean White Cards

**Files:**
- Modify: `frontend/src/components/ReportItemCard.vue`

- [ ] **Step 1: Update card top accent bar**

Line 144 — keep the colored top bar but reduce opacity:
```html
<div class="absolute top-0 left-0 w-full h-1 bg-[var(--card-accent)] opacity-40 group-hover:opacity-80 transition-opacity"></div>
```

- [ ] **Step 2: Update image area backgrounds**

Line 146 — change dark overlay:
```html
<div v-if="item.image_url" class="relative max-h-48 overflow-hidden bg-gray-100 border-b border-[var(--line)]">
```

Line 148 — update verified badge:
```html
<div v-if="item.has_verified_image" class="absolute top-2 right-2 bg-white/80 backdrop-blur-sm rounded-full px-2 py-1 flex items-center gap-1 text-[10px] text-[var(--status-ok)] border border-[var(--status-ok)]/30">
```

Line 152 — update fallback image area:
```html
<div v-else class="relative h-32 overflow-hidden bg-gray-50 border-b border-[var(--line)]">
```

- [ ] **Step 3: Update tag styles**

Lines 166-171 — replace dark tag backgrounds with light ones:
```html
<div class="flex flex-wrap items-center gap-2 text-[10px]">
  <span class="px-2 py-1 rounded-full bg-[var(--card-accent)]/8 text-[var(--card-accent)] border border-[var(--card-accent)]/15">{{ sectionLabel }}</span>
  <span v-if="categoryLabel" class="px-2 py-1 rounded-full bg-gray-100 text-gray-700 border border-gray-200">{{ categoryLabel }}</span>
  <span class="px-2 py-1 rounded-full bg-gray-100 text-[var(--text-muted)] border border-gray-200">{{ languageLabel }}</span>
  <span v-for="kw in keywords" :key="kw" class="px-2 py-1 rounded-full bg-gray-50 text-[var(--text-muted)] border border-gray-100">{{ kw }}</span>
</div>
```

- [ ] **Step 4: Update research signal box**

Line 178:
```html
<div class="p-3 rounded-lg bg-[var(--card-accent)]/5 border border-[var(--card-accent)]/10">
```

- [ ] **Step 5: Update source info area**

Line 186:
```html
<span class="px-2 py-1 rounded bg-gray-100">{{ friendlySourceName }}</span>
```

- [ ] **Step 6: Update trace panel**

Line 206:
```html
<div v-if="showBasis && hasBasis" class="trace-panel mt-2 p-3 rounded-lg bg-gray-50 border border-gray-100 text-xs leading-relaxed">
```

- [ ] **Step 7: Update hover shadow in scoped styles**

Replace lines 226-234:
```css
.report-card {
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.report-card:hover {
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
  border-color: #cbd5e1;
  transform: translateY(-3px);
}
```

- [ ] **Step 8: Verify card renders**

Confirm cards are white with subtle shadows, colored top accent, no glow effects.

---

### Task 5: SectionDivider — Light Capsules

**Files:**
- Modify: `frontend/src/components/SectionDivider.vue`

- [ ] **Step 1: Update divider line gradient**

Replace line 41:
```html
<div class="flex-1 h-px bg-gradient-to-r from-transparent via-gray-200 to-transparent flex items-center justify-center"></div>
```

- [ ] **Step 2: Update capsule badge**

Replace line 42:
```html
<div :class="['flex items-center gap-3 px-4 py-2 rounded-full border border-gray-200 bg-white transition-all duration-300', colorClass]">
```

Line 47:
```html
<div class="flex-1 h-px bg-gradient-to-r from-transparent via-gray-200 to-transparent"></div>
```

- [ ] **Step 3: Replace glow styles**

Replace the `<style scoped>` block (lines 51-57):
```css
.glow-academic { color: var(--accent-academic); border-color: rgba(43, 87, 151, 0.2); }
.glow-industry { color: var(--accent-industry); border-color: rgba(22, 163, 74, 0.2); }
.glow-policy { color: var(--accent-policy); border-color: rgba(124, 58, 237, 0.2); }
.glow-patent { color: #d97706; border-color: rgba(217, 119, 6, 0.2); }
.glow-wechat { color: #16a34a; border-color: rgba(22, 163, 74, 0.2); }
.glow-lab { color: #7c3aed; border-color: rgba(124, 58, 237, 0.2); }
```

- [ ] **Step 4: Verify divider renders**

Confirm section dividers are light capsules with colored text, no glow.

---

### Task 6: DashboardView — Light Buttons & Panels

**Files:**
- Modify: `frontend/src/views/DashboardView.vue`

- [ ] **Step 1: Update report type switcher**

Lines 168-187 — replace `bg-black/40` with light background:
```html
<div class="flex items-center gap-2 bg-gray-100 p-1 rounded-xl border border-gray-200 w-max">
```

For each button inside, update active/inactive classes. Active: `bg-[var(--accent-primary)] text-white`, inactive: `text-[var(--text-secondary)] hover:text-[var(--text-primary)]`. The current active class `text-black` should become `text-white` (since accent is now dark blue).

Lines 173, 179, 185 — replace:
```
:class="reportType === 'global' ? 'bg-[var(--accent-primary)] text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'"
```

- [ ] **Step 2: Update category filter buttons**

Lines 189-197 — same pattern:
```html
<div v-if="!isLabReport && !isAiReport" class="flex items-center gap-2 bg-gray-100 p-1 rounded-xl border border-gray-200 w-max mt-4">
```

- [ ] **Step 3: Update view mode switcher**

Lines 228-243:
```html
<div class="flex items-center gap-2 bg-gray-100 p-1 rounded-xl border border-gray-200 shrink-0">
```

Active button shadow (line 232): remove `shadow-[0_0_15px_rgba(100,180,255,0.3)]`.

- [ ] **Step 4: Update quality note box**

Line 246:
```html
<div v-if="qualityNote" class="text-sm text-[var(--text-secondary)] bg-blue-50 border border-blue-100 rounded-xl px-4 py-3">
```

- [ ] **Step 5: Update empty state**

Line 278:
```html
<div v-else class="flex flex-col items-center justify-center p-20 gap-4 bg-white border border-gray-200 rounded-2xl text-center">
```

- [ ] **Step 6: Verify dashboard renders**

Confirm all buttons, panels, and empty states use light colors.

---

### Task 7: CoverageGauge — Remove Glow

**Files:**
- Modify: `frontend/src/components/CoverageGauge.vue`

- [ ] **Step 1: Remove glow from progress bar**

Line 23 — change track background:
```html
<div class="h-2 w-full flex bg-gray-200 rounded-full overflow-hidden">
```

Lines 24-26 — remove `shadow-[0_0_10px_...]` from each segment:
```html
<div class="h-full bg-[var(--accent-industry)] transition-all duration-1000" :style="{ width: industryWidth }"></div>
<div class="h-full bg-[var(--accent-academic)] transition-all duration-1000" :style="{ width: academicWidth }"></div>
<div class="h-full bg-[var(--accent-policy)] transition-all duration-1000" :style="{ width: policyWidth }"></div>
```

- [ ] **Step 2: Remove glow from color dots**

Lines 31, 35, 39 — remove `shadow-[0_0_8px_...]`:
```html
<div class="w-3 h-3 rounded-sm bg-[var(--accent-industry)]"></div>
<div class="w-3 h-3 rounded-sm bg-[var(--accent-academic)]"></div>
<div class="w-3 h-3 rounded-sm bg-[var(--accent-policy)]"></div>
```

- [ ] **Step 3: Verify gauge renders**

Confirm progress bar is clean without glow effects.

---

### Task 8: AgentProgressPanel — Light Panel

**Files:**
- Modify: `frontend/src/components/AgentProgressPanel.vue`

- [ ] **Step 1: Update panel background**

Replace `.agent-progress-panel` style (lines 226-235):
```css
.agent-progress-panel {
  background: var(--bg-surface);
  border: 1px solid var(--border-glow);
  border-radius: 16px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  box-shadow: var(--shadow);
}
```

- [ ] **Step 2: Update info/warning/error bars**

Replace `.info-bar` (lines 287-296):
```css
.info-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--status-info);
  background: rgba(37, 99, 235, 0.06);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 12px;
}
```

Replace `.warning-bar` (lines 298-307):
```css
.warning-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--status-warn);
  background: rgba(217, 119, 6, 0.06);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 12px;
}
```

Replace `.error-bar` (lines 309-318):
```css
.error-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--status-error);
  background: rgba(220, 38, 38, 0.06);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 13px;
}
```

- [ ] **Step 3: Update step list area**

Replace `.step-list` (lines 336-348):
```css
.step-list {
  max-height: 220px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  background: #f8fafc;
  border: 1px solid var(--border-glow);
  border-radius: 8px;
  padding: 10px 12px;
}
```

Replace `.step-row.step-current` (lines 368-371):
```css
.step-row.step-current {
  background: rgba(43, 87, 151, 0.06);
  color: var(--text-primary);
}
```

- [ ] **Step 4: Update scrollbar in step list**

Replace (lines 350-357):
```css
.step-list::-webkit-scrollbar {
  width: 6px;
}
.step-list::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.15);
  border-radius: 3px;
}
```

- [ ] **Step 5: Update step summary border**

Line 327:
```css
border-top: 1px dashed var(--border-glow);
```

- [ ] **Step 6: Verify progress panel renders**

Confirm progress panel is light themed during report generation.

---

### Task 9: ChatBubble — Light Bubbles

**Files:**
- Modify: `frontend/src/components/ChatBubble.vue`

- [ ] **Step 1: Update user avatar**

Line 15 — update user avatar class:
```
:class="isUser ? 'bg-blue-50 border-[var(--accent-primary)] text-[var(--accent-primary)]' : 'bg-gray-100 border-gray-200 text-[var(--accent-policy)]'"
```

- [ ] **Step 2: Update bubble styles**

Line 22 — replace dark glass panel for assistant:
```
:class="isUser ? 'bg-[var(--accent-academic)] text-white rounded-tr-sm shadow-sm' : 'bg-white border border-gray-200 text-[var(--text-primary)] rounded-tl-sm prose max-w-none shadow-sm'"
```

- [ ] **Step 3: Update citation links**

Lines 33-34:
```
:class="isUser ? 'border-white/30 text-white' : 'border-gray-200 text-[var(--accent-policy)] hover:border-gray-300'"
```

- [ ] **Step 4: Remove glow-avatar**

Remove the `.glow-avatar` style (line 51) or replace:
```css
/* glow removed for light theme */
```

- [ ] **Step 5: Verify chat bubbles render**

Confirm user bubbles are blue, assistant bubbles are white with border.

---

### Task 10: ChatView — Light Chat Interface

**Files:**
- Modify: `frontend/src/views/ChatView.vue`

- [ ] **Step Step 1: Update conversation list sidebar**

Line 92:
```html
<aside class="w-72 hidden md:flex flex-col gap-4 border-r border-[var(--line)] pr-4 relative z-10">
```

Line 100 — update new conversation button:
```
class="p-2 rounded-lg bg-blue-50 text-[var(--accent-primary)] hover:bg-blue-100 transition-colors border border-blue-100"
```

Line 110-112 — update conversation item active/inactive:
```
:class="activeConversation?.id === conversation.id 
  ? 'bg-blue-50 border-blue-200' 
  : 'bg-gray-50 border-gray-100 hover:border-gray-200 hover:bg-gray-100'"
```

- [ ] **Step 2: Update chat header**

Line 125:
```html
<div class="px-6 py-4 border-b border-[var(--line)] bg-white flex items-center justify-between z-10 shrink-0">
```

- [ ] **Step 3: Update empty state**

Line 149:
```
class="w-16 h-16 rounded-2xl bg-blue-50 border border-blue-100 flex items-center justify-center mb-4"
```

- [ ] **Step 4: Update loading indicator**

Line 158:
```
class="flex items-center gap-2 text-[var(--accent-policy)] bg-white px-4 py-2 rounded-full border border-gray-200 shadow-sm"
```

- [ ] **Step 5: Update chat footer/input area**

Line 165:
```html
<div class="p-4 bg-white border-t border-[var(--line)] z-10 shrink-0">
```

Line 170 — textarea:
```
class="flex-1 min-h-[56px] max-h-40 bg-white border border-gray-200 rounded-xl px-4 py-3 text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent-primary)] focus:bg-white transition-all resize-y"
```

Line 177 — send button:
```
class="h-14 px-6 rounded-xl bg-[var(--accent-primary)] text-white font-bold flex items-center gap-2 transition-all hover:bg-[#1e3f73] disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
```

- [ ] **Step 6: Verify chat view renders**

Confirm chat interface is clean and light themed.

---

### Task 11: LoginView — Light Login Card

**Files:**
- Modify: `frontend/src/views/LoginView.vue`

- [ ] **Step 1: Update login form container**

Line 35:
```html
<form class="max-w-md w-full p-10 bg-white border border-gray-200 rounded-2xl shadow-lg" @submit.prevent="submit">
```

- [ ] **Step 2: Update input styles**

Lines 41, 45 — replace dark input classes:
```
class="block w-full mt-2 px-4 py-3 bg-white border border-gray-200 rounded-lg text-[var(--text-primary)] text-sm transition-[border-color,box-shadow] focus:outline-none focus:border-[var(--accent-primary)] focus:shadow-[0_0_0_3px_rgba(43,87,151,0.1)]"
```

- [ ] **Step 3: Update submit button**

Line 48:
```
class="block w-full px-6 py-3 bg-[var(--accent-primary)] text-white font-semibold text-sm rounded-lg cursor-pointer transition-[opacity,transform] hover:opacity-90 hover:-translate-y-0.5 disabled:opacity-50 disabled:cursor-not-allowed mb-3"
```

- [ ] **Step 4: Update toggle button**

Line 49:
```
class="block w-full px-6 py-3 bg-transparent text-[var(--text-secondary)] font-medium text-sm rounded-lg border border-gray-200 cursor-pointer transition-[background,color] hover:bg-gray-50 hover:text-[var(--text-primary)]"
```

- [ ] **Step 5: Verify login renders**

Confirm login page is clean white card centered on light background.

---

### Task 12: HistoryView — Light History

**Files:**
- Modify: `frontend/src/views/HistoryView.vue`

- [ ] **Step 1: Update sidebar list**

Line 35:
```html
<aside class="w-80 flex-shrink-0 flex flex-col gap-4 border-r border-[var(--line)] pr-4 relative z-10">
```

Lines 58-60 — update selected/unselected items:
```
:class="selected?.id === report.id 
  ? 'bg-white border-[var(--accent-industry)]/40' 
  : 'bg-gray-50 border-gray-100 hover:border-gray-200 hover:bg-white'"
```

Line 63 — remove glow from selected indicator:
```html
<div v-if="selected?.id === report.id" class="absolute left-0 top-0 bottom-0 w-1 bg-[var(--accent-industry)]"></div>
```

- [ ] **Step 2: Update main content area**

Line 82:
```html
<main class="flex-1 flex flex-col min-w-0 bg-white border border-[var(--line)] rounded-2xl overflow-hidden relative z-10 shadow-sm">
```

Line 84 — update header:
```html
<div class="relative shrink-0 border-b border-[var(--line)] bg-white overflow-hidden">
```

Line 103 — update scroll area:
```html
<div class="flex-1 overflow-y-auto p-6 md:p-8 scroll-smooth z-0 bg-gray-50">
```

- [ ] **Step 3: Verify history renders**

Confirm history page uses light colors throughout.

---

### Task 13: AgentTraceView — Light Trace

**Files:**
- Modify: `frontend/src/views/AgentTraceView.vue`

- [ ] **Step 1: Update trace sidebar items**

Lines 72-74:
```
:class="selectedRunId === run.id 
  ? 'bg-white border-[var(--accent-academic)]/40' 
  : 'bg-gray-50 border-gray-100 hover:border-gray-200 hover:bg-white'"
```

Line 77 — remove glow:
```html
<div v-if="selectedRunId === run.id" class="absolute left-0 top-0 bottom-0 w-1 bg-[var(--accent-academic)]"></div>
```

- [ ] **Step 2: Update trace main area**

Line 99:
```html
<main class="flex-1 flex flex-col min-w-0 bg-white border border-[var(--line)] rounded-2xl overflow-hidden relative z-10 shadow-sm">
```

Line 101:
```html
<div class="shrink-0 border-b border-[var(--line)] bg-white px-6 py-4 flex items-center justify-between z-10">
```

- [ ] **Step 3: Update timeline dot colors**

Line 132 — change `border-[#0a0e1a]` to `border-white`:
```
class="absolute -left-[31px] top-6 w-4 h-4 rounded-full border-4 border-white bg-white ring-1 transition-colors"
```

- [ ] **Step 4: Update thought/code block backgrounds**

Line 152 — thought block:
```
class="text-sm text-[var(--text-primary)] leading-relaxed italic bg-gray-50 p-3 rounded-lg border border-gray-100 border-l-[3px] border-l-[var(--accent-academic)]"
```

Line 162 — tool call block:
```
class="bg-gray-50 p-3 rounded-lg border border-gray-100 h-full overflow-hidden"
```

Line 172 — tool output block:
```
class="bg-gray-50 p-3 rounded-lg border border-gray-100 h-full overflow-hidden"
```

- [ ] **Step 5: Verify trace view renders**

Confirm trace view uses light backgrounds for code/thought blocks.

---

### Task 14: AdminView — Light Admin Panel

**Files:**
- Modify: `frontend/src/views/AdminView.vue`

- [ ] **Step 1: Update admin header icon**

Line 269:
```html
<div class="w-12 h-12 rounded-2xl bg-purple-50 flex items-center justify-center border border-purple-100 text-[var(--accent-policy)]">
```

- [ ] **Step 2: Update JSON textarea area**

Line 287:
```html
<div class="flex-1 p-4 bg-gray-50">
```

Line 288 — textarea:
```
class="w-full h-full bg-white border border-gray-200 rounded-lg text-xs font-mono text-[var(--text-secondary)] p-4 focus:border-[var(--accent-primary)] transition-colors focus:outline-none resize-none"
```

- [ ] **Step 3: Update config inputs**

Lines 304, 308, 312, 316, 320, 324 — replace `bg-black/40 border-white/10` with:
```
bg-white border-gray-200
```

Lines 329, 336 — checkbox labels:
```
class="flex items-center gap-3 text-sm text-[var(--text-primary)] bg-gray-50 p-4 rounded-xl border border-gray-200 select-none cursor-pointer"
```

Lines 345, 350, 354 — text inputs:
```
bg-white border-gray-200
```

- [ ] **Step 4: Update WeChat sync section**

Lines 374, 378, 388 — inputs:
```
bg-white border-gray-200
```

- [ ] **Step 5: Update quality stats cards**

Lines 421, 443:
```
bg-gray-50 border-gray-100
```

Lines 425, 429, 430, 437, 438 — form elements:
```
bg-white border-gray-200
```

Line 465 — monospace stats block:
```
class="text-xs text-[var(--text-secondary)] space-y-2 font-mono leading-relaxed bg-gray-50 p-4 rounded-lg border border-gray-100"
```

- [ ] **Step 6: Update pipeline run list**

Lines 487-488:
```
class="w-full text-left bg-gray-50 hover:bg-white border relative rounded-xl p-4 transition-all"
:class="selectedRunId === run.id ? 'border-[var(--accent-primary)]/40 hover:border-[var(--accent-primary)]/50' : 'border-gray-100 hover:border-gray-200 text-[var(--text-muted)]'"
```

Line 490 — remove glow:
```html
<div v-if="selectedRunId === run.id" class="absolute left-0 top-0 bottom-0 w-1 bg-[var(--accent-primary)]"></div>
```

- [ ] **Step 7: Update candidates section**

Line 525:
```
class="bg-gray-50 border border-gray-100 rounded-xl p-4 group"
```

- [ ] **Step 8: Verify admin renders**

Confirm admin panel uses light backgrounds for all sections.

---

## Verification

After all tasks are complete:

- [ ] Run `cd frontend && npm run dev` and open in browser
- [ ] Navigate through all pages: Dashboard, History, Chat, Admin, Login, AgentTrace
- [ ] Confirm no dark backgrounds, no neon glows, no particle animation
- [ ] Confirm Logo displays correctly in sidebar
- [ ] Check responsive behavior (mobile sidebar collapses)
- [ ] Run `cd frontend && npm run build` to ensure no build errors
