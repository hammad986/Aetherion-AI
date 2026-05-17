# Z61E — Settings Panel Maturity Report

## Current State Assessment

### Settings Surface Inventory (from templates/index.html audit)

The settings panel is accessible via the right panel / inspector. It contains:

#### Account & Security
- **Change Password** form: old password, new password, confirm — fully functional (calls `/api/auth/change-password`)
- **Delete Account** — modal with password/DELETE confirmation, calls `/api/auth/delete-account`
- **Active Sessions** list — shows all logged-in devices with revoke buttons

#### Provider Configuration
- **BYOK (Bring Your Own Key)** — per-session provider selection with API key input
- **Model selection** per provider (via plan mode: Lite/Pro/Elite)
- **Provider priority ordering** (Decision Intelligence Layer)

#### Theme
- Dark/light mode toggle — present and functional

#### Beta Labeling
- `EMAIL_API_KEY` references shown with honest "not configured" messaging in settings UI

### What Is Genuinely Functional
- Password change — real DB write, bcrypt re-hash
- Account deletion — cascades through all session/billing data
- Provider keys — stored per-session in server state, not persisted to DB (appropriate for BYOK)
- Theme preference — persisted to localStorage

### What Feels Incomplete
- **No keyboard shortcut reference panel** — shortcuts exist (Ctrl+Enter, Ctrl+Shift+E, etc.) but aren't documented in settings
- **No polling preference control** — users cannot adjust poll intervals
- **Provider config is in a modal/BYOK flow** — not a standalone settings section
- The settings surface is somewhat scattered across tabs rather than centralized

## Remaining Fake Surfaces
- None identified. All visible settings controls have real backend implementations.

## Remaining Trust Risks
- Showing `EMAIL_API_KEY` variable name in the UI is honest about the email feature being unconfured — this is the right call for beta transparency.

## Beta Settings Maturity Score: 7/10
Core account controls work. Provider config works. The panel is functional but not well-organized — settings are distributed rather than centralized. Acceptable for beta.
