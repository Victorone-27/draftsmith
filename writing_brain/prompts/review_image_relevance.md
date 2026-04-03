Check whether images serve the article's arguments rather than being decorative.

## Check dimensions

1. Slot responsibility — does each image have a clear informational responsibility (supporting a judgment, showing a case, explaining a relationship)
2. Anchor correspondence — does the image's anchor point to a specific paragraph in the article, not a vague topic
3. Information increment — would the reader understand one less layer of meaning if this image were removed (if not, the image is decorative)
4. Role match — does the image's actual content match its declared role (e.g., is a data_chart actually a data chart)

## Common issues

- Cover image is unrelated to the article topic (uses a generic tech image)
- Supporting images only "look related" but do not support any specific paragraph
- Multiple images convey the same thing (information redundancy)
- scene_photo role but uses an AI-generated image (should use a real photograph)
- Image anchor points to a paragraph that has been deleted or significantly revised

## Pass criteria

- Each image's anchor can be found as a corresponding paragraph in the article
- Cover image is directly related to main_claim
- No two images serve the same argument (unless the article genuinely requires it)

## Output

```
ok: true | false
issues: ["specific issue description"]
needs_replacement: ["filename of slot needing replacement"]
replacement_hint: ["replacement suggestion"]
```
