# Eval datasets

One JSONL file per repository. The first line is a header that pins the exact commit, so line numbers stay valid:

```json
{"_meta": {"repo": "AkmalMohammed-1/QueueCTL", "sha": "<full commit sha>", "source": "github"}}
```

`source` is `github` (download that commit) or `local:<path>` (index a directory in this repo, used for the
`tiny_repo` smoke test).

Every following line is one question:

```json
{"id": "q01", "question": "How are failed jobs retried?", "expected": [{"path": "src/worker.js", "start": 40, "end": 72}], "answerable": true}
{"id": "q49", "question": "Which OAuth provider is used?", "expected": [], "answerable": false}
```

- `expected` lists the line ranges a correct answer must come from. A retrieved chunk counts as a hit if it is in
  the same file and its lines overlap a range.
- Include about 10% unanswerable questions (`"answerable": false`, `expected: []`). They measure whether the model
  says "I don't know" instead of guessing.
- Write the questions by hand from reading the code at that commit, the way a new contributor would ask them.
  Don't generate them from the retriever's own output, or the eval only measures itself.

`tiny_repo.jsonl` is a pipeline smoke test on a 7-file fixture. Its numbers aren't meaningful, so never report them.
