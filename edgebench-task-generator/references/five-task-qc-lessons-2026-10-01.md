# Five-task QC retrospective: non-format lessons

Source: owner-provided 2026-10-01 review of five older EdgeBench samples. The
review's V2.4 naming rule, Harbor task fields, Smoldata Oracle user, platform
upload and Harbor delivery route do **not** supersede the current mandatory
Meta V3.0 / EdgeBench V1 scope and owner's SForge-only ZIP handoff. No old
task's status, score, personnel or customer material is carried into a new
customer package. These are process failure patterns, not new model results.

| Finding from the review | Current reusable pipeline treatment |
|---|---|
| A NOP script replaced the model-visible starter with an easier empty file | SForge audit checks that both NOP and unmodified starter snapshot hashes coincide and both score the declared baseline. Score the exact original file, not a substitute. |
| A historical real model submission changed score or validity on re-evaluation despite five repeat reference scores | Five reference repetitions alone are not sufficient to establish snapshot determinism. Re-evaluate retained model submissions with frozen Judge/context bytes, compare validity, raw metric, normalized score and named checks, including on the other native architecture. Record a task-bound post-run replay receipt; a mismatch blocks delivery rather than becoming a footnote. |
| Two differently packaged implementations had the same underlying scored task | Package namespace/task-hash uniqueness does not catch semantic duplication. Main reviewer compares upstream instance, objective, input cases, Judge/scorer and weights across candidate identities; one scored task counts once. |
| Frontier trials sometimes exceeded the achieved reference or saturated early | Do not call a measured reference a proven global optimum. Challenge the upper and difficulty claim, create a new frozen identity if scorer/anchors change, and keep prior model scores historical. Slow-but-flat and zero-only curves do not establish learnability. |
| Report claimed every scored record or best submission was shipped while package omitted some | Reconcile final ZIP inventory and README/QC counts against raw SForge run history, every scored report and retained best/checkpoint submission; preserve scope of omitted duplicate archives and never invent score/Step or treat infrastructure failure as model zero. |
| A score report asserted isolation or clean bytes while internal QA files or private build dependencies survived | Independently execute Work/Judge no-network and fresh Judge checks, scan ZIP plus external QC and compressed submissions, inspect an extracted final ZIP for unrelated review metadata, host paths, private domains/tokens and personal identifiers. A source-only package must be buildable online from shipped contexts and public pinned inputs. |

Items above needing **new actual task-bound receipts** remain incomplete until
measured. This reference is not the raw multi-customer QC report and is never
part of a customer ZIP.
