# Байт — Brand & Illustration Style

Source of truth for colour: `docs/BRAND.md` and the official site values in
`docs/BRAND-RESEARCH.md`. Reference renders: `assets/banner.jpg` (1254×627) and
`assets/byte.jpg` (1145×1374); their JPEG-sampled `#0237BB` is retired — it is not
the university blue.

Target canvas for category art: **1536×1024 (3:2)**.

---

## 1. Palette

### Core (≥ 90% of every image)

| Hex | Name | Role |
|---|---|---|
| `#0477D2` | TISBI Blue | Primary brand mass: hoodie, badges, pills, dominant shapes, sky field |
| `#444B54` | Ink | Text-grade dark, pants, outlines, neutral props |
| `#F4F8FF` | Ice | Preferred illustration background instead of pure white |
| `#FFFFFF` | Paper | Negative space, hood lining, laptop, UI cards, badge faces |
| `#041F73` | Navy Shadow | Shadows on blue, offsets under lettering, darkest brand tone |

### Warm accent (≤ 10%, danger + mascot)

| Hex | Name | Role |
|---|---|---|
| `#FD8749` | Byte Orange | Beak, flame core, hazard accents, one focal pop |
| `#D97039` | Burnt Orange | Shade of the accent: beak shading, emblem rims |
| `#A14020` | Rust | Orange shadow, outlines on warm shapes |
| `#FDD6AB` | Cream | Mascot face/muzzle; warm highlight, not a brand field |

### Support (≤ 2%, derived from the brand hues only)

| Hex | Name | Role |
|---|---|---|
| `#2A72F1` | Sky Blue | Lighter blue for depth layers and highlighted panels |
| `#A8D1FF` | Haze | Ice-blue glow, glass, light rays; 20–35% opacity shapes |
| `#5B6373` | Slate | Neutral metal, technical linework |
| `#838755` | Muted Olive | Nature/foliage only, desaturated, never vivid green |

Rules:
- Blue carries the brand: `#0477D2` ≥ 55% of pixel area. Orange is a spark of ≤ 10%,
  one focal pop per image, never a field colour. Never a 50/50 split.
- Ink is `#444B54`; no pure black and no other near-black substitute in brand art.
- No teal/cyan glow, no neon green, no purple/violet, no rainbow gradient.
- Red is not a brand colour: alarm states use orange on navy, not red fields.

---

## 2. Character: Байт

Anthropomorphic kiwi-like bird — a flightless, round, shaggy bird drawn as a student.

**Body & proportions**
- Stocky, chubby humanoid build. Broad shoulders, short thick legs, oversized blocky sneakers.
- Head ≈ 1/5 of figure height, round, slightly wider than tall.
- Head-to-shoulder ratio roughly 1:2 (shoulders with arms outread ~3 head widths).
- Arms short and rounded with three-fingered paws; standing, feet planted, slight contrapposto.
- Fluffy orange curled tail behind the legs, tuft at the tip.

**Head & face**
- No visible ears, no feathers drawn as individual strands — one shaggy crown silhouette.
- Crown/back of head: warm brown-orange. Face mask, cheeks, muzzle: Cream `#FDD6AB`.
- Signature feature: long, thick, drooping orange beak (`#FD8749`→`#D97039`, `#A14020` under-edge) that hangs past the chin. Rounded tip. One small nostril dot at the base.
- Eyes: two small, close-set, glossy dark ovals with big white catchlights. No sclera ring, no eyebrows drawn — the brown crown edge acts as a brow.
- Mouth: a short upturned smile line on the cream muzzle beside/below the beak.
- Expression baseline: friendly, slightly goofy, looking at the viewer.

**Outfit**
- Royal blue hoodie `#0477D2` with white hood lining and white drawstrings; big front kangaroo pocket.
- White `</>` glyph on the left sleeve/shoulder.
- Hexagonal TISBI badge on the chest: white outline hexagon, wordmark inside, small "ТИСБИ" below.
- Charcoal cargo pants `#444B54` with a thin blue side stripe.
- Blue-and-white chunky sneakers with the same hexagon mark.

**Props (canonical set)**
- Dark navy backpack on both shoulders, straps and buckles visible, small flag patch on the side pocket.
- Slim silver laptop (`#E6E9F0` body, white lid) clamped under one arm, TISBI hexagon on the lid.
- Blue lanyard with a white vertical ID card: hexagon logo, "PM 611", striped flag block.
- Small keychain tag on the belt.

**Gestures**
- Canonical hero pose: one arm raised in a thumbs-up, other arm holding the laptop, weight on one leg, grin.
- Secondary: presenting open palm (explaining), both hands on hips (confident), holding a card/phone (showing content).
- Always cheerful and competent. Never scared, never angry, never "hacker in a hood".

---

## 3. Illustration style

- **Technique:** 2D flat vector look with limited painterly shading. Shapes built from simple blobs, rounded rectangles, and clean arcs. No 3D renders, no glossy plastic, no perspective grids.
- **Line:** soft contour only, `#041F73` or `#444B54` tinted, 2–4 px at 1000 px height. Outlines on the mascot and foreground emblems; background layers have none. Never pure-black hatch or comic cross-hatching.
- **Shading:** one hard mid-tone + one soft gradient per shape. Highlights are soft airbrush crescents, never specular dots — except eye catchlights and the laptop lid.
- **Light:** single soft top-left key. Warm rim light only on orange/beak/flame edges.
- **Depth:** three planes — flat background wash, mid-plane blurred shapes (10–20% opacity), sharp foreground emblem. Background never competes with the subject.
- **Background for category art:** Ice `#F4F8FF` field with 2–3 large pale-blue organic shapes (`#A8D1FF` at 20–35%) and a soft vignette. Not dark navy, not a night scene, not a photo.
- **Composition:** one dominant emblem mass, centred (or centre-left with air on the right), inside 8% margins. Diagonal flow from upper-left to lower-right. Two or three small floating rounded-square icons max, all on the same soft drop shadow.
- **Shape language:** rounded, friendly, zero sharp spikes except deliberate hazard triangles. Corners ≥ 12% of shape width.
- **Texture:** none. No grain, no noise, no paper, no halftone.

---

## 4. Typography & layout cues (banner)

Not baked into illustrations — these are for HTML/canvas overlays around the art.

- **Display:** heavy brush-marker script, connected strokes, thick downstrokes and thin joins, soft terminals, slight bounce on the baseline. White fill, navy `#041F73` offset shadow (≈6 px at 100 px cap height), rotation −6° to −8°. Sticker-like lift.
- **Sub-badge:** fully rounded pill, `#0477D2` fill, white text, medium weight, sentence case, tight tracking.
- **Body:** clean humanist sans, 3 lines max, left-aligned, line height 1.35, white on blue or Ink on Ice.
- **Hierarchy:** display word ≈ 5× body size. One badge, one paragraph, nothing else.
- **Doodles:** hand-drawn thin strokes in white or Sky Blue — crown with three dots, sparkle dashes, a curved arrow pointing at the subject. One or two per layout, never a cloud of them.
- **Layout grid:** content column left, subject right; building/landmark detail only as a blurred mid-plane; 5% outer margin; full-bleed, no frame.

---

## 5. Do / Don't

**Do**
1. Keep official TISBI blue `#0477D2` dominant, orange `#FD8749` as a single focal spark.
2. Keep the mascot stocky and round — belly, short legs, big shoes.
3. Keep the drooping orange beak as the silhouette's read at thumbnail size.
4. Draw props as clean simplified shapes with one highlight.
5. Give every category art one hero object, readable at 230 px wide.
6. Use `#F4F8FF` ice backgrounds and pale blue depth shapes.
7. Use rounded-square floating icons with soft shadows.
8. Keep the muzzle expression friendly, even for danger topics — the bot reassures, it does not scare.
9. Frame scenes as TISBI student life in Kazan — campus, lecture hall, practice — not a generic office or anonymous city.

**Don't**
1. Don't render 3D, plastic, chrome, or teal neon (kills the brand).
2. Don't put the mascot in category art — only `hero.png` holds Байт.
3. Don't use a white/orange robot, a generic robot head, or an animal other than the kiwi-bird.
4. Don't touch the palette: no vivid green slime, no purple, no red-dominant fire, no gradient rainbow.
5. Don't add text, letters, watermarks, UI chrome, or Cyrillic labels inside generated art — text lives in HTML.
6. Don't draw graphic injury, blood, weapons, masked attackers, or realistic disasters; keep threat symbolic.
7. Don't use the Red Cross emblem (protected symbol). A white cross on a blue rounded square is the medical mark.
8. Don't outline background layers, add grunge, or fill the frame edge-to-edge with detail.
9. Don't stretch the mascot, shrink the head below 1/6 of height, or hide the beak behind props.
10. Don't mix line weights: one contour weight per image.
11. Don't depict Енотис or any raccoon: Енотис is the university's official mascot and never appears in Byte art.

---

## 6. Generation prompts

Shared brand frame (prepend to every positive prompt):

> TISBI student brand — University of Management "TISBI", Kazan, Tatarstan: a practical
> student-safety guide for university students. Palette is locked to official TISBI blue
> #0477D2 (dominant), Byte Orange #FD8749 (single focal, ≤10%), Ink #444B54 (linework and
> dark props), Ice #F4F8FF (background), white #FFFFFF and Navy Shadow #041F73. Campus
> cues are allowed only as soft background hints: Kazan, a university colonnade, a lecture
> hall. No Енотис, no raccoon, no other university mascot; Байт appears only in `hero.png`.

Shared negative block (append to every prompt):

> Negative: photorealistic, 3D render, CGI, plastic, chrome, teal or cyan glow, neon green, purple, violet, magenta, rainbow gradient, vivid red dominant, heavy black outlines, comic cross-hatching, grunge texture, noise, grain, text, letters, numbers, watermark, logo lockup text, UI screenshots, frames, borders, mascot, cartoon bird, kiwi, character with a beak, raccoon, Енотис, TISBI raccoon mascot, humans, faces, blood, gore, weapons, masked attackers, Red Cross emblem, cluttered composition, dark night background, drop-shadowed typography.

### Hero — `hero.png` (mascot allowed)
> Flat 2D vector-style brand illustration, 3:2, 1536×1024. Full-body mascot: a stocky anthropomorphic kiwi-like bird student named Байт — round shaggy brown-orange head, cream face mask, long drooping orange beak past the chin, two small glossy dark eyes with white catchlights, short thick legs, oversized blocky sneakers. Wearing a royal blue hoodie (#0477D2) with white hood lining and drawstrings, a white </> glyph on the sleeve, a white hexagonal university badge on the chest, charcoal cargo pants (#444B54) with a thin blue stripe, blue-and-white sneakers, a fluffy orange curled tail. Dark navy backpack on both shoulders, slim silver laptop clamped under one arm with a hexagon mark on the lid, blue lanyard with a white ID card. One arm raised in a friendly thumbs-up, weight on one leg, wide grin. Background: ice-white (#F4F8FF) field, two large pale blue organic shapes (#A8D1FF at 25%), a very soft blurred silhouette of Kazan's Kul Sharif mosque with blue domes at 12% opacity bottom-left, single soft top-left key light, soft airbrushed shading, no hard specular, rounded friendly shapes, clean 2–3 px Ink (#444B54) contour, generous margins, three small rounded-square icons floating right (paper plane, chat bubble, </>) on soft shadows. No text, no watermark.
> Negative: (shared block, but drop "mascot, cartoon bird, kiwi, character with a beak" from it for this prompt; keep everything else).

### 1. Fire — `fire.png`
> Flat 2D vector illustration, 3:2, 1536×1024, friendly safety-education emblem. Centre: a rounded-rectangle fire extinguisher in TISBI blue (#0477D2) with white (#FFFFFF) label panel and a burnt-orange (#D97039) handle, nozzle hose curling into a soft arc. Behind it, one stylised flame built from three rounded teardrop strokes in Byte Orange (#FD8749) fading to Burnt Orange (#D97039) with a Rust (#A14020) under-edge, plus two thin smoke curls in Haze (#A8D1FF). A rounded hazard triangle badge in the upper right with an exclamation mark cut out in white. Ice-white (#F4F8FF) background, two large pale blue (#A8D1FF at 25%) organic blobs, soft vignette. Composition centred with 8% margins, diagonal flow to lower right, one soft top-left key light, flat fills with one mid-tone and soft airbrush highlight crescents, clean Ink (#444B54) contour on foreground only, rounded corners, no sharp spikes, no characters, no text.

### 2. Terror / hostage — `terror.png`
> Flat 2D vector illustration, 3:2, 1536×1024, calm symbolic public-safety emblem — no violence. Centre: a rounded navy (#041F73) shield with a white keyhole-free outline, in front of it a small unmarked dark backpack lying on the ground with a folded orange (#FD8749) warning tag, ringed by a blue-and-white cordon tape loop (#0477D2 with white dashes) crossing the lower third. Upper left: one small floating rounded-square icon with a radio/alert waveform glyph in white on Sky Blue (#2A72F1). Ice-white (#F4F8FF) background, two large pale blue organic shapes at 22%, very soft blurred suggestion of a colonnade in the mid-plane at 10% opacity. Centred composition, 8% margins, soft top-left key, flat fills, single contour weight, rounded friendly shapes conveying preparedness rather than fear, no characters, no weapons, no text.

### 3. Tech / industrial accident — `tech.png`
> Flat 2D vector illustration, 3:2, 1536×1024, technosphere-safety emblem. Centre: an isometric-free flat silhouette of a small industrial plant — two rounded smokestacks, one pipe elbow, a valve wheel — in TISBI blue (#0477D2) and Sky Blue (#2A72F1), with a cream (#FDD6AB) pressure gauge dial whose needle points into a Rust (#A14020) warning arc. A cracked gear shape sits behind at 25% opacity. Two thin Haze (#A8D1FF) steam curls rise. Small floating rounded-square icon upper right with a wrench glyph in white on blue. Ice-white (#F4F8FF) background with two large pale blue organic blobs, soft vignette. Centred, 8% margins, diagonal flow, soft top-left key, flat fills plus one mid-tone per shape, airbrush highlights only, Ink (#444B54) contour on foreground, rounded corners, no characters, no text.

### 4. Nature / natural disasters — `nature.png`
> Flat 2D vector illustration, 3:2, 1536×1024, natural-hazard emblem with muted, reassuring tones. Centre: a rounded low hill in TISBI blue-tinted grey-blue, cut by one clean zigzag fault line in Navy Shadow (#041F73) with a thin Byte Orange (#FD8749) inner edge; above it a compact storm cloud built from three overlapping rounded lobes in Sky Blue (#2A72F1) with a single white (#FFFFFF) lightning bolt, plus two Muted Olive (#838755) rounded tree silhouettes at the base, deliberately desaturated. Thin Haze (#A8D1FF) rain strokes, five max. Ice-white (#F4F8FF) background, two large #A8D1FF organic blobs at 25%, soft vignette. Centred, 8% margins, soft top-left key, flat fills, one contour weight, rounded friendly shape language, no vivid green, no characters, no text.

### 5. Medical / first aid — `medical.png`
> Flat 2D vector illustration, 3:2, 1536×1024, first-aid emblem. Centre: a white (#FFFFFF) rounded-square first-aid kit with a TISBI blue (#0477D2) lid band and handle, opened slightly to reveal a cream (#FDD6AB) interior with two rolled bandages and a small pair of rounded scissors in Slate (#5B6373). Floated above and to the right: a blue rounded square holding a white equal-armed cross — NOT a red cross, not the protected Red Cross emblem. Behind the kit, a soft ECG pulse line in Sky Blue (#2A72F1) crosses the frame at 30% opacity. Ice-white (#F4F8FF) background, two large pale blue organic shapes at 22% opacity, soft vignette. Centred composition with 8% margins, soft top-left key light, flat fills, one mid-tone plus airbrush crescent highlight, single Ink (#444B54) contour weight, rounded corners, no characters, no text.

### 6. Chemical hazard — `chem.png`
> Flat 2D vector illustration, 3:2, 1536×1024, chemical-safety emblem. Centre: a round-bottom laboratory flask with a white (#FFFFFF) glass body, a Byte Orange (#FD8749) liquid filling the lower third, a Burnt Orange (#D97039) meniscus line and a Rust (#A14020) shadow at the base; two rounded bubbles rising inside. Beside it, a compact white hazmat hood-and-filter mask suggested as three rounded shapes with a Slate (#5B6373) filter cartridge, and a thin Haze (#A8D1FF) vapour curl behind at 40% opacity. Upper right: a rounded diamond badge in blue (#0477D2) with a white exclamation glyph. Ice-white (#F4F8FF) background, two large pale blue organic blobs at 25%, soft vignette. Centred, 8% margins, soft top-left key, flat fills, one contour weight, rounded friendly shapes, absolutely no green slime, no skull motif, no characters, no text.

### 7. Radiation — `rad.png`
> Flat 2D vector illustration, 3:2, 1536×1024, radiation-safety emblem. Centre: a large trefoil symbol in Byte Orange (#FD8749) with a Burnt Orange (#D97039) inner edge, mounted on a white (#FFFFFF) rounded square with a TISBI blue (#0477D2) frame, tilted 8 degrees. In front, a small handheld Geiger counter in navy (#041F73) with a cream (#FDD6AB) display showing three ascending bars, plus two stacked rounded shielding blocks in Sky Blue (#2A72F1). One thin Haze (#A8D1FF) concentric ring behind the trefoil at 25% opacity. Ice-white (#F4F8FF) background, two large pale blue organic shapes, soft vignette. Centred composition, 8% margins, diagonal flow, soft top-left key, flat fills with one mid-tone, airbrush highlights only, single Ink (#444B54) contour weight, no glow, no characters, no text.

### 8. Cybersecurity — `cyber.png`
> Flat 2D vector illustration, 3:2, 1536×1024, cybersecurity emblem. Centre: a white (#FFFFFF) padlock with a TISBI blue (#0477D2) body and a Sky Blue (#2A72F1) shackle, its keyhole cut out in navy (#041F73), sitting on a rounded blue shield outline. Behind it, a simplified terminal window in white with a blue title bar and three dots, showing two rounded code-bracket shapes `</>` in blue and one Byte Orange (#FD8749) cursor block; a single thin phishing hook curve in Burnt Orange (#D97039) arcs toward the padlock at 35% opacity and is clipped by the shield. Ice-white (#F4F8FF) background, two large pale blue organic blobs at 25%, soft vignette. Centred, 8% margins, soft top-left key, flat fills, single contour weight, rounded friendly shapes, no hooded figure, no binary rain, no green matrix, no characters, no text.

---

## 7. Consistency checklist

- Palette limited to the table above; blue ≥ 55% of pixel area.
- One subject, readable at 230 px. Silhouette test: fill all shapes black, subject still reads.
- Background is Ice, not navy. No glow bloom.
- No text anywhere. No mascot except `hero.png`.
- Orange appears exactly once as the focal accent.
- Corner radii consistent across the set; equal 8% margins.