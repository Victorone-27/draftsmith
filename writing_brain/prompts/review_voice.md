Check whether the article sounds like the author expressing ideas, rather than a model padding content.

## Check dimensions

1. Template feel — presence of filler transitions like "值得注意的是", "不难发现", "某种程度上", "在当下这个时代", "总的来说", "换句话说"
2. List-speak — using "首先/其次/最后" or "第一/第二/第三" to drive the entire article instead of causal progression
3. Hollowness — paragraphs that could be deleted without affecting the article's argumentation (indicating padding)
4. Paragraph rhythm — three or more consecutive paragraphs with identical structure (mechanical repetition of opening judgment + development + closure)
5. Label residue — presence of template labels like "标题：", "导语：", "正文：", "标题备选", "总结："

## Scoring logic

voice_score = 100 - filler_hits × 18 - repeated_paragraphs × 20 - template_hits × 15

template_hits include: "标题：", "导语：", "总结：", "首先", "其次", "最后"

## Pass criteria

- voice_score ≥ 70
- Article body does not start with `**标题`
- No template label residue

## Output

```
ok: true | false
voice_score: number
filler_count: number
template_hits: number
repeated_paragraphs: number
problems: ["specific problem description"]
```

## Common failure modes

- Opening with "在当下这个时代" as preamble instead of directly stating the judgment
- Every paragraph follows the mechanical structure of "X is important. Because Y. Therefore Z."
- Ending with "总的来说" without giving the reader new cognition
- Article body contains generation artifacts like "标题备选：" or "导语："
