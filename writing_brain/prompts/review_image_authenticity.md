Check whether images are obviously AI-generated or have visible generation artifacts.

## Check dimensions

1. Authenticity risk — does the image have typical AI generation features (overly smooth, unnatural lighting, deformed details)
2. Poster risk — does the image look like a marketing poster rather than editorial imagery (over-designed, large text titles, neon colors)
3. Style consistency — do multiple images in the same article maintain consistent style, avoiding one realistic and one cyberpunk
4. Source credibility — do public-source images have verifiable provenance (source_url, license)

## High-risk signals

- Deformed human faces/hands
- Garbled text or unreadable pseudo-text
- Overly symmetrical composition
- Unnatural depth of field or light source direction
- Neon blue-purple, 3D rendering, cyberpunk style
- "Real photographs" without source attribution (may be AI-generated disguised as real)

## Pass criteria

- Cover image must not have obvious AI artifacts (it is the first image the reader sees)
- Public-source images must have source_url and license
- Generated-source images must be labeled as AI-generated

## Output

```
ok: true | false
risk_level: low | medium | high
issues: ["specific issue description"]
needs_replacement: ["filename of slot needing replacement"]
```
