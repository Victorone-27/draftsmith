Check whether the article's key judgments have sufficient evidence support.

## Check dimensions

1. Evidence signals — does the text contain specific cases, data, scenarios, comparisons, citations, or other supporting material
2. Unsupported assertions — which judgments have only conclusions without support ("X is important" but no explanation why)
3. Evidence-judgment alignment — does the cited evidence actually support the preceding judgment, or does it change the topic
4. Evidence density — is the total number of evidence signals sufficient to support the article's judgment density

## Signal word reference

Evidence: "比如", "例如", "案例", "场景", "数据", "一个很直接的例子", "根据", "调查显示"
Counterexample: "但如果", "反过来", "例外是", "不适用于"
Citation: "某某说", "某某认为", "报告显示"

## Pass criteria

- evidence_hits ≥ max(1, min(2, number of evidence_items))
- That is: at least 1 evidence signal; if research_pack has 2+ evidence_items, then at least 2

## Output

```
ok: true | false
evidence_hits: number
required_minimum: number
unsupported_claims: ["judgment in paragraph N lacks evidence"]
```

## Common failure modes

- Entire article is judgments and opinions without a single concrete example or data point
- Examples exist but lack connection to the judgment (reader must guess why the example was given)
- Evidence is concentrated in one paragraph while other paragraphs are all unsupported assertions
- Using "as everyone knows" or "it's well known" as a substitute for actual evidence
