Check whether the article forms continuous argumentation rather than opinion stacking.

## Check dimensions

1. Judgment clarity — does the article present a debatable core judgment within the first two paragraphs
2. Causal chain completeness — do judgments have derivation relationships (because/therefore/this means)
3. Contrast effectiveness — do "not X but Y" contrasts genuinely distinguish two different things
4. Example support — do examples directly support the preceding judgment rather than changing the topic
5. Claim-support gap — which paragraphs give only conclusions without support (no causal chain, contrast, or example after the judgment)

## Signal word reference

Causal: "因为", "所以", "因此", "导致", "结果是", "这意味着"
Contrast: "不是", "而是", "相比", "反过来", "问题在于"
Support: "比如", "例如", "案例", "场景", "数据", "一个很直接的例子"
Qualifier: "前提是", "代价是", "风险是", "条件是"

## Pass criteria

- Total argument signals ≥ 5
- Cover at least 2 of the 3 categories: causal, contrast, support
- claim-support gap ≤ 1 (at most 1 judgment paragraph lacking support)

## Output

```
ok: true | false
signal_total: number
category_hits: {causal: N, contrast: N, support: N, qualifier: N}
claim_support_gaps: number
gap_locations: ["paragraph N gives only conclusion without support"]
```

## Common failure modes

- Entire article is "X is important", "Y is key" without a single paragraph explaining why
- Contrasts exist but both sides describe the same thing (pseudo-contrast)
- Examples and judgments lack connecting sentences — the reader must guess the relationship
