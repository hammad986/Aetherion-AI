# UI/UX VISUAL DISCIPLINE AUDIT
# Phase Z4 Workspace Polish
# Generated: 2026-05-15

## 1. Navigation Rail (`nx-shell-navrail`)
- **Current State**: Uses inline CSS (`grid-area:navrail;`), lacks disciplined 48px enforcement. Icons are 20px but padding and active states are inconsistent. Tooltips are missing native OS styling.
- **Action**: Restructure to exactly 48px width. Implement a muted gray active state (no neon borders). Remove text labels. Vertically align icons with a flex column layout and bottom-aligned settings icon.

## 2. Topbar Hierarchy (`nx-shell-topbar`)
- **Current State**: Disconnected execution controls. The model selector looks like a dropdown pill but is too heavy. The project header (`Nexora / Workspace`) uses high-contrast purple icons which feel like a marketing site.
- **Action**: Consolidate into three clear zones: Left (Breadcrumb/Context), Center (Execution: Run/Stop/Model), Right (Utilities: Command Palette, Inspector, Settings). Use VSCode/Linear density (28px-32px height buttons, `13px` or `12px` typography). Remove `#bc8cff` accents from the breadcrumb in favor of subtle grayscale.

## 3. Command Palette (`nx-palette`)
- **Current State**: Full screen backdrop, floating center screen. Feels pasted on.
- **Action**: Style the trigger in the topbar to look like a search bar or discreet native shortcut. The palette itself should align higher (top 20%) with strict macOS Spotlight/Linear styling: subtle border, heavy blur backdrop, precise typography hierarchy.

## 4. Empty State (`nx-idle-hero`)
- **Current State**: Uses a giant "A" logo, marketing copy ("What do you want to build?"), and a grid of highly visible chips. Feels like a landing page.
- **Action**: Redesign to be a hyper-minimal terminal/editor empty state. Remove the logo. Use a simple, centered command input or shortcut hint: `Ctrl+K to open commands` or `Ctrl+Enter to execute`. Remove the bright chips in favor of subtle grayscale suggestions.

## 5. Visual Noise & Typography
- **Current State**: Inconsistent border colors (`#27272A` vs `#30363d`). `font-size: 14px` mixed with `11px` randomly.
- **Action**: Standardize to `Inter` at `13px` for UI text, `JetBrains Mono` at `13px` for code/terminal. Normalize all panel borders to a single token (e.g., `rgba(255, 255, 255, 0.1)`). Remove neon purple borders on active tabs.
