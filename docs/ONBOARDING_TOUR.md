# INPROFIC onboarding tour media and calls to action

The onboarding tour is intentionally lightweight. Its original animated flow artwork lives at the top of `templates/core/_onboarding_tour.html` and requires no image files. A separate lower layer renders a compact module-specific operational preview using the active tenant vertical. Optional custom animated illustrations can replace **only that lower preview** on a step-by-step basis; the original top artwork remains untouched.

## Where to add future tour illustrations

Put the asset in:

```text
apps/core/static/core/tour/
```

Recommended names match the tour step, for example:

```text
apps/core/static/core/tour/inventory.webp
apps/core/static/core/tour/procurement.webm
apps/core/static/core/tour/commerce.mp4
apps/core/static/core/tour/delivery.gif
```

Supported by the renderer: ordinary image formats including PNG/GIF/WebP, and looping muted MP4/WebM video.

After adding a file, open:

```text
templates/core/_onboarding_tour.html
```

and find the `tourMedia` object. Set only the matching filename, for example:

```javascript
const tourMedia = {
  dashboard:'',
  search:'',
  inventory:'inventory.webp',
  procurement:'procurement.webm',
  // ...
};
```

The file name is resolved under `core/tour/` through Django's static URL. A full `https://...` URL can also be used when the illustration is deliberately hosted on a CDN or R2.

Then deploy normally and run the project's normal `collectstatic` step. Do **not** upload tour files directly into an ephemeral Render filesystem outside the deployed source/static pipeline, because a new deploy or instance replacement can remove them.

## Display behaviour

- Automatic first-time tours wait about 2.4 seconds after the workspace finishes opening before the card appears, so the newly authenticated user sees the dashboard first. An explicit `?tour=1` replay remains immediate.
- The step progress bar begins at 0% on first open, grows in both directions with Previous/Next, and keeps its brand gradient visibly moving; each step change also sends a highlight across the filled portion.
- The final step changes the primary action from **Next** to **Finish**.
- The original INPROFIC animated flow artwork always remains first.
- A compact tenant-vertical-aware module preview appears immediately underneath that artwork when no custom file is configured.
- Wholesale and retail previews stay stock-first; production-led tenants receive material/batch/output examples.
- An optional custom file replaces that lower native preview only.
- If a configured file fails to load, INPROFIC falls back to the native module preview rather than showing a broken image.
- The tour card deliberately keeps its bright palette in dark workspace theme. Tour text colours do not switch to the workspace's dark-theme text palette.
- Custom media also remains at full opacity in dark mode.

## Calls to action

Every tour step has a short **Try this next** instruction. These are defined beside each step in the same `definitions` array in `_onboarding_tour.html`. They are intentionally action prompts rather than navigation buttons so the user can complete the whole tour without accidentally leaving it midway.

When changing a tour tip, keep its CTA concrete: one useful next action, not another description of the module.

## Step keys

The current media keys are:

```text
dashboard
search
inventory
procurement
production
sales
commerce
pos
delivery
finance
reports
users
settings
```

Adding or removing a step does not require a database migration. Increment `CURRENT_TOUR_VERSION` in `apps/core/onboarding.py` only when a future tour change is important enough that previously completed memberships should be offered the new tour automatically.
