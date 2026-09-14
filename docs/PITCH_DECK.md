# INPROFIC pitch deck

The editable 16:9 product deck is available as
[`INPROFIC_Pitch_Deck.pptx`](INPROFIC_Pitch_Deck.pptx). Its 15 slides cover the
problem, connected operating model, INPROFIC acronym, five verticals,
inventory, procurement, production, commerce, finance, controls, reporting,
architecture, subscriptions, Founder-managed promotions, safe live demos and value proposition.

All slide content is built from editable PowerPoint text boxes, shapes and
diagrams. It is not a collection of flattened slide images.

## Open as an editable Google Slides presentation

1. Open Google Drive and select **New → File upload**.
2. Upload `docs/INPROFIC_Pitch_Deck.pptx`.
3. Open the uploaded file with **Google Slides**.
4. Select **File → Save as Google Slides** to create a native Slides copy.

Review font substitution and the rotated cover mark after import. The deck uses
the broadly available Aptos typeface and standard editable shapes to minimize
conversion differences.

## Rebuild after changing the content

```bash
python -m pip install -r requirements-deck.txt
python scripts/build_pitch_deck.py
```

The builder overwrites only `docs/INPROFIC_Pitch_Deck.pptx`.
