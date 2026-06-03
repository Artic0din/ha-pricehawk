# Web Interface Guidelines

Guidance and best practices for designing great user experiences on the web. Adapted from Apple's Human Interface Guidelines for use in browser-based interfaces.

As you design interfaces, keep these principles in mind:

- **Hierarchy** — Establish a clear visual hierarchy where controls and interface elements elevate and distinguish the content beneath them.
- **Harmony** — Align typography, spacing, motion, and colour so the interface feels cohesive across breakpoints, browsers, and devices.
- **Consistency** — Adopt familiar web conventions and maintain a consistent design that continuously adapts across viewport sizes, input methods, and assistive technologies.

---

## Colour

Judicious use of colour enhances communication, evokes your brand, provides visual continuity, communicates status and feedback, and helps people understand information.

On the web, colour must hold up across an enormous range of displays, ambient lighting, browser rendering quirks, and user-controlled accessibility settings. Design with this variability in mind.

### Best practices

**Avoid using the same colour to mean different things.** Use colour consistently throughout your interface, especially when it communicates status or interactivity. If you use your brand accent to indicate that a borderless button is interactive, don't apply the same colour to noninteractive text.

**Make sure all colours work in light, dark, and high-contrast contexts.** Modern browsers expose user appearance preferences through `prefers-color-scheme` and `prefers-contrast`. Use CSS custom properties and `light-dark()` to define both light and dark variants:

```css
:root {
  color-scheme: light dark;
  --color-surface: light-dark(#ffffff, #1c1c1e);
  --color-text:    light-dark(#1c1c1e, #f2f2f7);
}
```

**Test under different lighting and display conditions.** Colours look darker and more muted in bright surroundings, and brighter and more saturated in dark rooms. Test on commodity laptop screens, OLED phones, calibrated displays, and projectors if relevant.

**Test across browsers and colour profiles.** Colour rendering differs between Chrome, Firefox, and Safari, and between sRGB and P3 displays. Use the `color()` function when you need wide-gamut colours, but always provide an sRGB fallback:

```css
.accent {
  background: #007aff;
  background: color(display-p3 0 0.478 1);
}
```

**Consider how imagery and translucency affect nearby colours.** Backdrop filters, semi-transparent overlays, and large hero images can shift the perceived colour of adjacent UI. Sample your interface against real content, not just blank canvases.

**Prefer the native colour picker for colour input.** `<input type="color">` gives a consistent experience and respects user preferences. Reach for a custom picker only when you need swatches, alpha, or other features it doesn't support.

### Inclusive colour

**Never rely on colour alone** to differentiate between objects, indicate interactivity, or communicate essential information. Pair colour with text labels, icons, patterns, or position. Required form fields shouldn't just be red — they should also carry a label or icon indicating the requirement.

**Maintain sufficient contrast.** Aim for WCAG 2.2 AA at minimum (4.5:1 for body text, 3:1 for large text and non-text UI). Strive for AAA (7:1) where you can, particularly for small text. Check both states of interactive elements — hover, focus, disabled — not just the resting state.

### Semantic colour tokens

**Avoid hard-coding raw colour values** throughout your stylesheets. Define semantic tokens once and reference them everywhere. This is the web equivalent of dynamic system colours — when the meaning changes (a new brand, a new theme, a high-contrast variant), you update one place.

```css
:root {
  /* Foundations */
  --blue-500: #007aff;
  --gray-50:  #f2f2f7;

  /* Semantic — reference foundations */
  --color-link:              var(--blue-500);
  --color-surface-secondary: var(--gray-50);
  --color-separator:         light-dark(#e5e5ea, #38383a);
}
```

**Don't reuse a token outside its semantic meaning.** Don't use `--color-text-secondary` as a background. Don't use `--color-separator` as a text colour.

---

## Materials and depth

Materials are visual effects — blur, transparency, layering — that create a sense of depth between foreground and background. On the web, they're produced primarily through `backdrop-filter`, `background-color` with alpha, and `box-shadow`.

### Best practices

**Use materials to convey structure, not decoration.** A blurred toolbar that sits above scrolling content communicates that it's a persistent control layer. A drop shadow on a card communicates that it's lifted off the surface beneath it. If a material doesn't carry meaning, simplify.

**Help ensure legibility on translucent surfaces.** Backdrop-blurred panels can drift in apparent colour depending on what's behind them. Bump up text weight, add a subtle translucent base layer, or fall back to a solid background when blur isn't supported:

```css
.toolbar {
  background: rgb(255 255 255 / 0.7);
  backdrop-filter: blur(20px) saturate(180%);
}

@supports not (backdrop-filter: blur(20px)) {
  .toolbar { background: rgb(255 255 255 / 0.95); }
}
```

**Match material weight to the context.** Thicker, more opaque materials provide better contrast for small text and fine details. Thinner, more translucent materials let people retain their sense of place by hinting at what's behind. Choose deliberately.

---

## Layout

A consistent layout that adapts across viewport sizes makes your interface approachable. People expect familiar relationships between controls and content to help them discover and use features.

### Best practices

**Group related items.** Use negative space, background shapes, separators, or subtle colour changes to show when elements belong together and to separate distinct sections. Keep content and controls clearly distinguishable.

**Give essential information enough room.** Don't bury the primary action under chrome and decoration. Secondary information can move into expandable sections, side panels, or subsequent views.

**Extend backgrounds to the edges of the viewport.** Backgrounds, hero imagery, and full-bleed sections should reach the edges of the browser window — letterboxing or unintended margins look broken. Scrollable regions should scroll cleanly to the bottom and sides.

**Mind the fold, but don't obsess over it.** People scroll. Place the most important content near the top, but design as if the user will keep going, because they will.

### Visual hierarchy

**Differentiate controls from content.** Controls should read as interactive — through elevation, fill, outline, or a distinct surface. Content should read as content. When a button looks like a paragraph, people miss it; when a paragraph looks like a button, people click it.

**Place items to convey relative importance.** People read top-to-bottom and leading-to-trailing. Put the most important items near the top and along the leading edge. Account for right-to-left languages in your layout direction.

**Align components.** Alignment makes an interface look organised and helps people scan. Pick a consistent grid (4px, 8px) and stick to it. Indentation and alignment together communicate hierarchy.

**Use progressive disclosure.** When you can't display everything at once, hint at what's hidden — partial card edges that suggest more content below, disclosure arrows, "Show more" affordances. Don't hide important information behind interactions people won't discover.

**Give controls breathing room.** Cramped controls are hard to hit, hard to tell apart, and hard to understand. Target sizes should be at least 24×24 CSS pixels (WCAG 2.2), with 44×44 strongly preferred for primary touch targets.

### Adaptability

Every interface needs to adapt when the viewport changes. The web has more variability than any native platform — from 320px phones to ultrawide monitors, with users zooming, rotating, resizing, and bringing assistive tech.

Common variations to handle:

- Viewport widths from ~320px to 3000px+
- Portrait and landscape orientations
- Touch, mouse, keyboard, voice, and switch input
- Browser zoom from 50% to 400%+
- User font-size overrides
- Locale-driven layout direction (LTR/RTL), text length, date and number formatting
- Reduced motion, reduced transparency, high contrast preferences

**Design layouts that adapt gracefully while remaining recognisable.** Use fluid grids, container queries, and `clamp()` for typography. Avoid fixed pixel widths for primary content. Respect `safe-area-inset-*` so notched and rounded devices don't clip your UI.

**Be prepared for text-size changes.** Use relative units (`rem`, `em`, `ch`) rather than pixels for type. Make sure your layout doesn't break when users hit Ctrl+ a few times.

**Preview across viewport sizes, input modes, and locales.** Test the smallest and largest layouts first — the middle usually takes care of itself. Test long-string translations (German is your friend here), RTL, and very large user font sizes.

**Scale imagery thoughtfully.** When the viewport shape changes, scale media so important visual content stays visible. Use `object-fit: cover` and `object-position` to control crop. Provide `srcset` and `sizes` so the browser downloads the right resolution.

### Containers, safe areas, and breakpoints

Use a small set of breakpoints driven by content, not devices. Modern layout primitives — flexbox, grid, container queries — let you build components that adapt to their context rather than the viewport:

```css
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 1rem;
}

@container (min-width: 600px) {
  .card { flex-direction: row; }
}
```

Respect safe areas on mobile browsers so toolbars, status bars, and gesture areas don't overlap your content:

```css
.app-shell {
  padding-top:    env(safe-area-inset-top);
  padding-bottom: env(safe-area-inset-bottom);
}
```

---

## Icons

An effective icon expresses a single concept in a way people instantly understand.

Web interfaces use icons throughout — in navigation, buttons, status indicators, form fields. Unlike app icons, which can be rich and detailed, interface icons (glyphs) should be simple shapes with limited colour.

### Best practices

**Create a recognisable, highly simplified design.** Too much detail makes icons confusing at small sizes. Aim for familiar visual metaphors directly related to the action or concept.

**Maintain visual consistency across all icons.** Whether you use a single icon library (Lucide, Phosphor, Heroicons, Tabler) or a custom set, all icons should share size, weight, stroke width, corner radius, and perspective. Mixing icon families looks sloppy.

**Match the visual weight of icons to adjacent text.** A 1.5px stroke icon next to 400-weight body text looks balanced. The same icon next to 700-weight headlines looks anaemic. Adjust accordingly.

**Use SVG.** SVG icons scale crisply at any resolution, can be styled with CSS (`color`, `currentColor`, `fill`), and animate easily. Inline SVG is best for icons that need styling; sprite sheets work well for static icons; icon fonts are largely obsolete and have accessibility downsides.

```html
<button class="icon-button" aria-label="Settings">
  <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
    <path d="..." fill="currentColor" />
  </svg>
</button>
```

**Add padding for optical alignment.** Asymmetric icons (arrows, play triangles) often look off-balance when geometrically centred. Nudge them or add transparent padding to the SVG viewBox until they feel right.

**Don't provide selected-state icons when the component handles it.** A toggle, button, or nav item that already changes appearance on activation doesn't need a separate filled-vs-outline icon pair unless the meaning genuinely shifts.

**Use inclusive imagery.** Prefer gender-neutral figures, avoid culturally specific gestures or symbols that don't translate, and consider how icons read across regions and languages.

**Include text in icons only when essential.** If you need a character (e.g., "B" for bold), make sure it's localised. For passages of text, use abstract shapes — and provide a mirrored version for RTL contexts.

**Always include an accessible name.** Decorative icons get `aria-hidden="true"`. Meaningful icons get `aria-label` on the button or link that contains them.

---

## Branding

Web interfaces express brand identity in ways that make them recognisable while feeling at home in the browser and giving users a consistent experience.

### Best practices

**Use your brand voice and tone consistently.** Every piece of UI copy — buttons, errors, empty states, tooltips — should sound like the same product. Build a writing reference and use it.

**Choose an accent colour.** Pick one accent that signals interactivity and brand throughout the UI. Make sure it meets contrast requirements against your surface colours in both light and dark modes.

**Consider a custom font carefully.** Custom fonts express brand but cost performance. If you use one, subset it, preload it, and make sure it supports the weights and language ranges you need. Pair a brand display font with a system stack for body text if the custom font isn't optimised for small sizes:

```css
:root {
  --font-display: "BrandSans", system-ui, sans-serif;
  --font-body:    system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
```

**Let branding defer to content.** Don't waste prime real estate on a giant logo when users came for the task or the content. Brand through accent colour, typography, voice, and interaction — not by stamping the logo everywhere.

**Use familiar patterns even in stylised interfaces.** A highly branded UI can still feel approachable when buttons look like buttons, links look like links, and forms behave the way people expect.

**Don't repeat the logo throughout the interface.** Users know which site they're on. One logo in the header is almost always enough.

**Avoid splash screens as branding moments.** Loading should feel fast, not ceremonial. If you have onboarding to do, do it through real content and helpful UI, not a brand reveal.

### Logo and badge usage

Consistent logo and badge presentation protects brand recognition and ensures legibility across contexts.

#### Minimum size

- **Print:** Minimum height of 10 mm.
- **Onscreen:** Minimum height of 30 pixels.

Below these sizes, the logo loses legibility and detail. If you need a smaller representation, use a simplified mark or favicon variant designed for the size.

#### Clear space

Maintain clear space around the logo or badge equal to one-quarter the height of the badge on all sides.

```
┌─────────────────────────────┐
│         clear space         │
│     ┌───────────────────┐   │
│     │                   │   │
│     │       LOGO        │   ← clear space = 1/4 × logo height
│     │                   │   │
│     └───────────────────┘   │
│         clear space         │
└─────────────────────────────┘
```

In CSS, treat this as a non-negotiable margin or padding around the logo's container:

```css
.logo {
  --logo-height: 48px;
  height: var(--logo-height);
  margin: calc(var(--logo-height) / 4);
}
```

**Do not place graphics, type, photographs, or illustrations inside the clear space area.** This includes navigation items, taglines, decorative elements, and background imagery. The clear space exists so the logo reads cleanly — fill it with anything and you defeat the purpose.

---

## Dark mode

Dark mode is a user-level preference, not an app-level one. The browser exposes it through `prefers-color-scheme`, and your site should respect it by default.

### Best practices

**Avoid offering a site-specific appearance toggle as the only option.** If you do offer a toggle (and many sites should, for users whose OS preference doesn't match their need in your context), default to the user's system preference and persist their choice. Don't force people to set it twice.

**Make sure your site looks good in both modes.** People can switch between them at any time — including automatically based on time of day. Design and review both together, not as an afterthought.

**Test legibility carefully in dark mode with high contrast.** Dark text on dark backgrounds gets worse, not better, when contrast settings increase. Audit your dark palette against high-contrast and reduced-transparency preferences.

**Use a permanently dark UI only when it serves the content.** Media-viewing experiences, immersive editors, and certain games benefit from a dark-only treatment. Most interfaces should respect user preference.

### Dark mode colours

Dark mode isn't simply "invert the light palette." Some colours invert; many don't. Pure black surfaces feel harsh and OLED-grimy. Use slightly elevated dark surfaces (`#1c1c1e`, `#2c2c2e`) and slightly desaturated brights.

**Use semantic colour tokens that adapt automatically.** Define tokens once with light and dark values; let `light-dark()` or `prefers-color-scheme` resolve at runtime.

**Aim for sufficient contrast in both modes.** WCAG ratios still apply — 4.5:1 minimum for body text, 7:1 ideal. Test small text especially carefully on dark backgrounds.

**Soften pure-white imagery.** Images with bright white backgrounds glow against dark UI. Slightly dim them, give them a subtle border, or provide a dark-mode variant.

### Icons and images

**Use SVG icons that adapt via `currentColor`.** They'll inherit the surrounding text colour and switch automatically.

**Provide light and dark variants for raster artwork when needed.** Logos and illustrations often need separate light-mode and dark-mode files. The `<picture>` element with `prefers-color-scheme` media queries handles this cleanly:

```html
<picture>
  <source srcset="logo-dark.svg" media="(prefers-color-scheme: dark)" />
  <img src="logo-light.svg" alt="Brand" />
</picture>
```

---

## Images

Images on the web have to look right across resolutions from 1× phones to 3× retina displays and wide-gamut monitors.

### Resolution and scale

Provide multiple resolutions and let the browser choose:

```html
<img
  src="hero-800.jpg"
  srcset="hero-400.jpg 400w, hero-800.jpg 800w, hero-1600.jpg 1600w"
  sizes="(max-width: 600px) 100vw, 50vw"
  alt="Description"
/>
```

### Formats

| Image type | Preferred format |
|---|---|
| Photos | AVIF, with WebP and JPEG fallbacks |
| Illustrations with flat colour | SVG |
| Icons and logos | SVG |
| Screenshots with text | PNG or WebP (lossless) |
| Animated content | WebP, AVIF, or `<video>` over GIF |

Use `<picture>` to provide modern formats with fallbacks:

```html
<picture>
  <source srcset="photo.avif" type="image/avif" />
  <source srcset="photo.webp" type="image/webp" />
  <img src="photo.jpg" alt="..." />
</picture>
```

### Best practices

**Always provide alt text.** Decorative images get `alt=""`. Meaningful images get descriptive text. Don't repeat surrounding caption text.

**Set explicit dimensions or aspect ratios.** Use `width`, `height`, or CSS `aspect-ratio` to reserve space and prevent layout shift while the image loads.

**Lazy-load below-the-fold images.** Use `loading="lazy"` on non-critical images. Keep above-the-fold hero images eager and consider `fetchpriority="high"`.

**Test on real devices.** Images that look pristine in design tools can show banding, compression artefacts, or colour shifts on actual phone and laptop screens.

---

## Motion

Thoughtful motion brings the interface to life — conveying status, providing feedback, and enriching the experience. Careless motion distracts, disorients, and makes some users physically unwell.

### Best practices

**Add motion purposefully.** Don't animate for the sake of it. Animation should clarify cause and effect, smooth transitions between states, or provide feedback. If it doesn't do one of those things, cut it.

**Respect `prefers-reduced-motion`.** This is non-negotiable — for some users, large motion triggers vestibular disorders, migraines, or nausea. Reduce or remove animation when requested:

```css
.modal {
  transition: opacity 200ms ease, transform 200ms ease;
}

@media (prefers-reduced-motion: reduce) {
  .modal {
    transition: opacity 200ms ease;
    transform: none;
  }
  *, *::before, *::after {
    animation-duration:  0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

Don't strip all feedback — fades and brief opacity changes are usually fine. The problem is parallax, large translates, scale, and rotation.

### Providing feedback

**Match motion to gesture and expectation.** If a panel slides in from the right, it should slide out to the right when dismissed. If a card scales up to open, it should scale back down to close. Mismatched in/out animations feel broken.

**Keep feedback brief.** Most UI animations should land between 150ms and 400ms. Anything longer starts to feel sluggish on repeat use. Use shorter durations (100–200ms) for small UI changes and slightly longer (300–400ms) for larger transitions.

**Avoid animating frequent interactions.** A button that animates dramatically every time it's clicked gets old fast. Subtle is usually correct.

**Let people cancel or skip motion.** Don't gate user interaction behind a 600ms animation. People should be able to act before the animation completes.

---

## Onboarding

Onboarding helps people get started, but the best onboarding is no onboarding — interfaces people understand simply by using them.

### Best practices

**Teach through interactivity.** People learn by doing, not by reading. Where possible, let users perform real actions in a safe environment instead of clicking through instructional slides.

**Prefer in-context tips over upfront tours.** A tooltip that appears the first time someone hovers a feature is more useful than a five-step tour they'll forget. Show help where and when it's needed.

**Keep prerequisite onboarding brief.** If you really do need a setup flow, make it fast, focused, and enjoyable. Three steps beats ten. Don't try to teach everything — teach enough to start.

**Make tutorials optional and re-accessible.** Let people skip an intro on first visit, but make it easy to find later through help or settings.

**Don't lecture people about the browser.** They know what a button is and how to scroll. Onboarding should cover your product, not the platform.

### Additional content

**Defer non-essential setup.** Provide sensible defaults so people can use the product immediately. Account customisation, notification preferences, integrations — these can wait until users have a reason to care.

**Don't let downloads block experience.** Keep initial payloads small. Code-split, defer non-critical JavaScript, and let the core experience load before the optional bells and whistles.

**Avoid front-loading legalese.** Cookie banners, terms acceptance, and similar disclosures should be present and clear, but they shouldn't be the first thing a user sees if it can be avoided.

### Permission and credential requests

**Ask for permissions in context.** Don't request notification, location, or camera access the moment someone lands. Ask when the feature that needs the permission is about to be used, and explain why.

**Let people try before they sign up.** Where business model permits, give people a taste of value before pushing them through registration. Conversion is higher when the product has proven itself first.

---

## Typography

Typographic choices help you display legible text, convey hierarchy, communicate important content, and express brand.

### Ensuring legibility

**Use font sizes most people can read.** Body text should generally be at least 16px on screen. Smaller is acceptable for secondary content but never for the main reading experience.

**Test legibility in real contexts.** Long-form reading at arm's length on a laptop differs from a glance at a phone in sunlight. If text is hard to read, try larger sizes, better contrast, or fonts designed for screen use (system fonts are reliably good).

**Avoid very thin font weights.** Ultralight, Thin, and Light weights can look elegant in mockups and fall apart in production at small sizes. Stick to Regular, Medium, Semibold, and Bold for UI.

**Use a fluid type scale.** Combine `clamp()` with the viewport unit for responsive typography:

```css
:root {
  --text-base: clamp(1rem, 0.5vw + 0.875rem, 1.125rem);
  --text-h1:   clamp(2rem, 4vw + 1rem, 3.5rem);
}
```

### Conveying hierarchy

**Adjust weight, size, and colour to emphasise.** Hierarchy is most effective when it uses two or three of these in combination — size alone often isn't enough.

**Minimise the number of typefaces.** Two is usually plenty. Three is the upper limit. More than that and your interface starts to feel like a ransom note.

**Maintain hierarchy at every text size.** When users zoom or change their default font size, the relationship between H1, H2, and body should hold. Use relative units throughout.

**Prioritise important content during text-size changes.** Not every label needs to scale equally. Tab labels, navigation, and metadata can stay relatively smaller; body content and primary actions should scale most.

---

## Writing

The words in your interface are part of the experience. Treat copy with the same care you give visual design.

**Note on personal vs product voice.** These guidelines apply to *product UI copy* — labels, buttons, errors, empty states, tooltips, microcopy. They do not describe how the product owner writes in chat, commits, notes, or other personal contexts. If you (the developer) prefer lowercase-everything, casual phrasing, or other stylistic habits in your own writing, that's a personal voice — keep it out of the product. The product follows its own consistent voice and capitalisation rules as defined below.

### Getting started

**Determine your voice.** Who are you talking to? What words are familiar? How should people feel? A banking dashboard might sound careful and exact; a learning app might sound encouraging and playful. Write a short voice guide and reference it.

**Match tone to context.** Voice stays steady; tone shifts. An error that locks someone out of their account needs a different tone from a celebratory completion screen — but both should sound like the same product.

**Be clear.** Choose words that are easily understood. Cut anything that doesn't earn its place. When in doubt, read it aloud. If you stumble, rewrite.

**Write for everyone.** Use plain language. Avoid jargon, gendered terms, and idioms that don't translate. Consider how copy will read for someone using a screen reader and for someone reading a localised version.

### Best practices

**Consider each screen's purpose.** Most important information first. Keep paragraphs short. Break long content across screens or expandable sections.

**Use action-oriented labels.** Verbs are almost always better than vague labels. "Save", "Send", "Delete" beat "Submit", "OK", "Continue" — except in flows where "Continue" genuinely is the next step.

**Avoid "Click here" for links.** Link text should describe the destination: "Read the full announcement", not "Click here to read more". This matters for skim-readers and is critical for screen reader users.

**Build language patterns.** If you use "Save" in one place, don't use "Apply" elsewhere for the same action. Pick a term per concept and stick to it.

**Pick a capitalisation rule and apply it consistently.** Title Case for buttons feels formal; Sentence case feels modern and human. Either is fine. Mixing them looks careless.

**Use possessive pronouns sparingly.** "Favourites" is usually clearer than "Your favourites". Avoid "we" in error messages — it's vague and often unhelpful. "Unable to load content" beats "We're having trouble loading this."

**Adapt copy to device context.** A label that fits on desktop might wrap awkwardly on mobile. Test your copy at narrow widths. Don't say "click" when users might be tapping or swiping.

**Empty states should guide.** A blank screen is an opportunity to teach. Tell people what they're looking at, why it's empty, and what to do next. Provide a clear primary action.

**Write helpful error messages.** Tell people what went wrong, why, and how to fix it. "Choose a password with at least 8 characters" beats "Invalid password". Place errors near the relevant field, not in a banner at the top of the form.

**Choose the right delivery method.** Inline validation, toast, banner, modal — each carries different urgency. Reserve modals for things that genuinely block progress. Use toasts for confirmation. Use banners for persistent state.

**Keep settings labels practical.** Describe what the setting does when on. Don't make people interpret abstractions. If a label needs explanation, add a short description below it.

**Show hints in form fields.** Placeholder text shouldn't replace labels (it disappears on focus, which is hostile to memory and accessibility). Use a visible label plus optional hint text. Show format examples where useful: `name@example.com`.

---

## Charting data

Charts communicate complex information efficiently and add visual interest. Used well, they help people understand trends, compare values, and make decisions.

### Best practices

**Use a chart when you want to highlight something about a dataset.** Charts draw attention — make sure what they reveal is worth that attention. For raw lookup, a sortable table is often better.

**Keep charts simple; let people opt into detail.** Resist the urge to cram everything in. Use progressive disclosure — hover for details, click to expand, filter to narrow.

**Make every chart accessible.** Charts must be more than pictures. Provide:

- An accessible name and description on the chart container
- A summary of key takeaways in surrounding text
- A data table alternative (visible or available on request)
- Keyboard navigation for interactive elements
- Sufficient contrast and patterns alongside colour for series

### Designing effective charts

**Prefer common chart types.** Bar, line, and area charts are familiar. Novel chart types add cognitive load — use them only when they genuinely communicate something common types can't.

**Help people read novel charts.** If you must use an unusual visualisation, introduce it. Brief annotations, legends, or a "How to read this chart" affordance go a long way.

**Examine the data from multiple angles.** Show totals, averages, comparisons, and individual values where they help. Different viewers care about different perspectives.

**Use descriptive text.** Titles, subtitles, and annotations should reinforce the takeaway, not just label the axes. "Sales up 12% year over year" beats "Sales".

**Size charts to their content.** Tiny charts hide detail. Oversized charts waste space. Match dimensions to what the chart needs to communicate and how users will interact with it.

**Be consistent across related charts.** Same dataset shown in two ways should share colours, scales, and annotations. Differences should mean something — don't introduce visual variation arbitrarily.

---

## Entering data

When you need information from users, design forms that make it easy to provide without making mistakes.

### Best practices

**Get information from the system or context where possible.** Don't ask for what you already have. Don't ask for what you can detect (timezone, locale, theme). Don't ask for what you can infer (country from IP, address from postcode lookup).

**Be clear about what you need.** Use visible labels. Add helper text for non-obvious fields. Show format examples. Mark required fields explicitly — don't make users guess.

**Use secure input for sensitive data.** `<input type="password">` for passwords. Consider autocomplete attributes to support password managers (`autocomplete="current-password"`, `autocomplete="one-time-code"`).

**Never prepopulate passwords.** Always require users to enter them or use platform authentication.

**Offer choices instead of free text where possible.** Dropdowns, radios, and segmented controls are often faster and less error-prone than typing. But use them only when the option set is genuinely fixed — autocomplete handles open-ended choice better than a 200-item dropdown.

**Support paste and autofill.** Don't disable paste on email or password fields (it actively harms password manager users). Use proper `autocomplete` and `name` attributes.

**Validate as users type, gently.** Real-time validation prevents the dreaded "fix everything at the end" experience. Validate on blur for most fields; validate on input only for things with strict format rules (and show errors after the user finishes, not on the first keystroke).

**Use the right input types.** `type="email"` for email, `type="tel"` for phone, `type="number"` with `inputmode` for numbers. This gives mobile users the right keyboard and lets browsers validate.

**Make required fields obvious.** Don't disable the submit button silently until the form is valid — show what's missing.

---

## Loading

The best loading experience ends before users notice it started.

### Best practices

**Show something fast.** Blank screens read as broken. Render the page shell, navigation, and placeholders immediately; fill in content as it arrives. Skeleton screens or content placeholders beat spinners for perceived performance.

**Let users do other things while content loads.** Loading shouldn't be modal unless it has to be. Render what you have and stream the rest. Let users start typing, scrolling, or navigating where possible.

**Optimise the critical path.** Inline critical CSS, defer non-critical scripts, preload key assets, lazy-load below-the-fold media. Measure with real user monitoring, not just synthetic tests.

**If loading takes time, give people something to do or look at.** Long imports, big uploads, or complex calculations are sometimes unavoidable. Show progress, offer tips, or let users continue with other work while the operation completes.

### Showing progress

**Use determinate progress when you know how long.** A real progress bar with a real percentage. Don't fake it — users notice.

**Use indeterminate progress when you don't.** A spinner or pulsing bar signals work without a false promise of completion time.

**Match the indicator to the operation.** A button-level spinner for a form submission. A page-level loader for navigation. A skeleton for content. Don't put a full-page modal spinner on a 200ms operation.

---

## Modality

Modal interfaces present content in a dedicated state that blocks interaction with everything else and requires explicit dismissal.

Modals can:

- Surface critical information that requires acknowledgment
- Confirm destructive or significant actions
- Help users complete a focused subtask without losing their place
- Provide an immersive view of media or content

### Best practices

**Use modality only when there's a clear benefit.** Modals interrupt. Every modal is a small tax on the user. Use them when the benefit outweighs the disruption.

**Keep modal tasks simple and short.** A multi-step wizard inside a modal is usually wrong. If a task needs depth, give it a dedicated page or panel.

**Don't build an app inside a modal.** Hierarchical navigation inside a modal disorients users. One level deep is fine; two is suspect; three is wrong.

**Use a full-screen or large modal for in-depth media or complex tasks.** Image lightboxes, document editors, and onboarding flows benefit from filling the viewport.

**Always provide an obvious way to close.** A visible close button, Escape key support, and click-outside-to-dismiss (for non-destructive modals) are table stakes. Implement focus trapping while the modal is open and return focus to the trigger on close.

**Confirm before discarding work.** If closing a modal would lose unsaved data, ask first. Don't trap users — give them a clear way to save, discard, or cancel.

**Name the modal's task.** A title at the top of the modal helps users orient. If the task is long, supporting text can describe what's happening.

**Dismiss one modal before showing another.** Stacked modals are a code smell. The only exception is a confirmation dialog for a destructive action initiated inside a modal — and even that should be considered carefully.

### Accessibility

Modal dialogs are one of the most-failed accessibility patterns on the web. Use the native `<dialog>` element where possible — it handles focus, Escape, and the inert state of background content for free:

```html
<dialog id="confirm">
  <h2>Delete this item?</h2>
  <p>This action can't be undone.</p>
  <form method="dialog">
    <button value="cancel">Cancel</button>
    <button value="confirm" class="danger">Delete</button>
  </form>
</dialog>
```

---

## Searching

People search to find content within your site, within a specific section, or within an open document.

### Best practices

**If search is important, make it prominent.** A persistent search bar in the header or a dedicated search section communicates that finding things matters. Keyboard shortcut (Cmd/Ctrl+K) is increasingly expected for app interfaces.

**Aim for one global search location.** Users shouldn't have to figure out which of five search boxes to use. A single global search that scopes intelligently usually beats multiple local searches.

**Local search makes sense when the scope is obvious.** A search field at the top of a filterable table or inside a specific section can complement global search — but make the scope crystal clear.

**Use placeholder text to indicate scope.** "Search docs", "Search transactions", "Search messages and contacts" all communicate what users will get back.

**Display the current search scope.** When users have narrowed to a specific area, show that visibly. Allow easy expansion back to global search.

**Provide suggestions.** Recent searches, popular searches, and live suggestions as users type all reduce typing and surface useful entry points.

**Consider privacy when showing history.** Recent search history can leak personal context to anyone looking at the screen. Provide a clear way to clear history, and consider whether to show it at all on shared devices.

**Support keyboard navigation.** Arrow keys to move through results, Enter to select, Escape to dismiss. This is the expected pattern for search interfaces.

---

## Settings

Users expect interfaces to just work — but they also appreciate the ability to customise.

### Best practices

**Provide sensible defaults.** The default experience should be the best experience for the largest number of users. Most users never change settings — make sure they don't have to.

**Minimise the number of settings.** Every setting is a decision users have to make and a piece of UI you have to maintain. Cut anything that isn't pulling its weight.

**Make settings discoverable but not intrusive.** A clearly labelled link in the user menu, footer, or sidebar is enough. Don't put settings in the primary navigation unless customisation is core to your product.

**Avoid settings for things you can detect.** Theme preference, language, timezone — start with the browser's preference and let users override only if needed.

**Respect system-level preferences.** If the OS is in dark mode and the user hasn't set a site preference, use dark mode. If they've reduced motion at the system level, reduce motion. Don't override what the user already told their device.

### General settings

**Put infrequently-changed options in a settings page.** Account details, default views, notification preferences — things users set once and forget. These belong in a dedicated settings area.

### In-context options

**Surface task-specific options near the task.** Showing/hiding columns in a table, sorting a list, filtering results — these don't belong in a settings page. They belong as controls on the screen where they matter.

In-context options are discoverable, immediate, and reversible. Settings-page options are abstract, deferred, and easy to forget. Choose accordingly.

---

## A note on accessibility

Accessibility isn't a separate section — it's woven through every guideline above. To summarise the core obligations:

- Meet WCAG 2.2 AA at minimum, AAA where practical
- Provide text alternatives for non-text content
- Support keyboard navigation for everything
- Respect user preferences: `prefers-color-scheme`, `prefers-reduced-motion`, `prefers-contrast`, font size, zoom
- Use semantic HTML; reach for ARIA only when semantics can't express the pattern
- Test with screen readers, keyboard-only, and at high zoom
- Maintain visible focus indicators on all interactive elements

An interface that excludes users isn't a well-designed interface, however polished it looks.
