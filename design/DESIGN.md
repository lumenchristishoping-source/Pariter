# Pariter — Design Spec (locked)

This is the source of truth for Pariter's visual design. Build the real
app UI to match this **strictly** — do not deviate from these tokens or
component rules without an explicit decision to update this spec.

Reference mockup: [Pariter Group Chat](https://claude.ai/code/artifact/b6a4fd52-4e26-499f-814a-ea4747c3a497)
Raw source: `design/mockups/group-chat.dc.html`

## Theme

Dark theme only. Three tones, strictly:

- **Black** — background
- **Grey** — surfaces, borders, shadows, glow, secondary text
- **Brushed gold** (`#CCBA78`, not generic gold `#D4AF37`) — accent only

### The one hard rule

Brushed gold is used **only** for icons, buttons, and small highlights
(status dots, an active-speaker ring, a glow behind a filled button).
It is **never** used for:

- The chat input pill (background or border)
- Message bubbles (AI or user)
- Large fills or backgrounds of any kind

Grey is used to add depth — shadows, ambient blurred glow blobs, borders
— not as a flat decorative color.

## Color tokens

```css
--bg:            #09090b;   /* screen background */
--border:        rgba(255,255,255,0.07);
--text:          #EDEAE2;   /* warm off-white, primary text */
--text-dim:      #9A9A9E;   /* secondary text */
--text-faint:    #6C6C71;   /* tertiary / system notes */

--bubble-ai:        #1B1C20;
--bubble-ai-border: rgba(255,255,255,0.06);
--bubble-user:       #232428;

--gold:      #CCBA78;   /* brushed gold accent — icons/buttons/highlights only */
--gold-soft: rgba(204,186,120,0.22);   /* glow behind gold elements */
```

Avatar fills use distinct grey shades per agent (not color-coded hues) —
e.g. `#2A2B30`, `#35363B`, `#1E1F23` — to keep strictly to the
black/grey/gold palette while still differentiating participants.

## Typography

System font stack only — this is a native-feeling utility app, not a
marketing surface:

```css
font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif;
```

- Title: 16px / weight 600
- Subtitle / meta: 12px, `--text-dim`
- Message body: 14px / line-height 1.45
- Name label above a bubble: 11.5px, `--text-dim`
- System notes / dividers: 11px, `--text-faint`

## Components

### Top bar

- Padding `18px 16px 14px`, bottom border `1px solid var(--border)`,
  soft downward shadow (`0 8px 24px rgba(0,0,0,0.35)`).
- Left: circular icon button (back chevron, gold stroke) + title stack
  (title + subtitle). Subtitle carries a small gold "live" dot
  (`box-shadow: 0 0 6px var(--gold)`) when agents are active.
- Right: circular icon button (overflow, gold stroke).
- Icon buttons: 36px circle, `rgba(255,255,255,0.04)` fill, `1px solid
  var(--border)`. The button background is neutral grey-glass — only the
  icon glyph inside is gold.

### Avatar

- 32px circle, grey fill (per-agent shade), initials in `--text`.
- **Active/speaking state**: `2px solid var(--gold)` ring +
  `box-shadow: 0 0 10px var(--gold-soft)`. This is the only avatar
  state that uses gold — it signals "this agent is responding now."

### Message bubble

- AI (left-aligned): `--bubble-ai` fill, `1px solid
  --bubble-ai-border`, `border-radius: 16px 16px 16px 4px` (tail toward
  avatar).
- User (right-aligned, no avatar): `--bubble-user` fill,
  `border-radius: 16px 16px 4px 16px`.
- Name label sits above the bubble, not inside it.
- Typing/streaming indicator: three pulsing dots
  (`--text-dim`, staggered `blink` keyframe animation) inside a bubble
  shell, shown for an agent about to speak.
- System events (e.g. "X joined the discussion") are plain centered
  text, `--text-faint`, no bubble.

### Floating input pill

- Fixed to the bottom, sitting over a gradient scrim
  (`transparent → var(--bg)`) so it floats above the transcript rather
  than sitting in a solid bar.
- Glass pill: `border-radius: 999px`, `background: rgba(255,255,255,0.055)`,
  `backdrop-filter: blur(22px) saturate(160%)`,
  `border: 1px solid rgba(255,255,255,0.13)`.
- Glow/depth (grey/white only, never gold):
  `inset 0 1px 0 rgba(255,255,255,0.08)`,
  `0 14px 40px rgba(0,0,0,0.55)`,
  `0 0 44px rgba(255,255,255,0.06)`.
- A subtle glass highlight sliver (`::before`, radial white gradient)
  near the top-left sells the glass material.
- Left: an **@-mention icon** (gold stroke) — not a paperclip/attach
  icon. Middle: placeholder text (`--text-dim`). Right: circular send
  button, 36px, **filled solid gold**, dark icon glyph
  (`#0B0A08`) for contrast, gold-tinted drop shadow
  (`0 4px 14px rgba(204,186,120,0.35)`).

### Ambient glow

Two large blurred circles (`filter: blur(70px)`) behind the content —
one soft white/grey near the top, one faint gold-tinted one near the
bottom — for atmosphere. Low opacity (`0.05–0.07`), never a visible
hard edge.

## Icons

Inline SVG only, stroke-based (no emoji, no icon fonts), consistent
~1.8–2px stroke weight on a 24px viewBox. Gold stroke (`#CCBA78`)
everywhere an icon appears in the chrome (top bar, pill).

## Layout

Mobile-first, phone frame reference size 390×844. No fake OS chrome
(no drawn status bar or virtual keyboard) — the real device supplies
those.
