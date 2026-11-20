# evals

Evaluation data for Atlas (lesson 6.8; agents follow in lesson 8.9). The runner is `atlas eval`, in the `atlas` package; run it from `atlas/`.

| File | What it holds |
|---|---|
| `ignite.jsonl` | One case per line: a question about PyTorch-Ignite and the files a good answer must come from. A case with no files is one Atlas should decline. Checked against Ignite at commit `0abbcab`. |
| `thresholds.json` | The lowest acceptable score for each metric; CI fails below them. |
| `hand_grades.jsonl` | Answers a person graded (is every claim supported by its sources?), used to measure how often the judge model agrees. |

```bash
uv run atlas eval --retrieval-only                       # search only: seconds, no chat model
uv run atlas eval --thresholds ../evals/thresholds.json --hand-grades ../evals/hand_grades.jsonl --report report.json
```

The full run takes about 20 minutes on a laptop CPU with the local models. CI runs it in `.github/workflows/evals.yml`, after indexing and embedding Ignite at the pinned commit.
