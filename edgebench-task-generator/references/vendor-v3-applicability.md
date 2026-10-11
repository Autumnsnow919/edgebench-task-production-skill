# Current vendor requirements: applicability decisions

Primary documents supplied by the owner:

- `Meta Vendor Data Spec V3.0.zip`, SHA-256
  `b405dee192d5fcf0f2268b5792e33c43bc412c98ac67de9b74c046ab6fca6513`;
  the normative text is `Meta Vendor Data Spec V3.0.md` inside it.
- `EdgeBench Vendor Dataset Requirements.md`, current **Requirement — V1**.
  SHA-256 `783ff89e064f88f75d28ab72967b8389a0f762d422909d33f4e939fc6ce39538`.
  Its appended `[Archived] Initial Round Requirement` is historical, not an
  alternative requirement set.

Both documents are mandatory inputs. Potentially inapplicable Meta clauses
and conflicts with owner decisions are resolved explicitly with the owner,
not silently inferred. The rollout harness is SForge, while the user-selected
customer format is either **SForge-only** or **Harbor**. Use
`sforge-audit`/`package-samples` or `harbor-audit`/`export-harbor` according
to that choice. The current external file handoff is decided below; any future
platform-upload channel remains pending. This matrix is not a task acceptance
or platform-upload receipt.

## Delivery-format decision

The user selects **SForge-only** or **Harbor** before rollout and the selection
is bound to the frozen task hash. SForge-only delivers task JSON,
`BENCHMARK.yaml`, independent Work/Judge Dockerfiles and contexts, original
English task text and verified scored evidence. Harbor delivers unpacked
Harbor task directories with its independent agent/verifier contexts and
Harbor-specific validation evidence. The unselected format is not silently
treated as passed.
The selected format uses an English customer package and a separate English
quality inspection report. Chinese translations are supplied privately for
owner review and excluded from customer bytes. The external README guide's
upper/lower model pairing and eight-item 100-point quality score are not
transplanted to this single-cohort EdgeBench scope.

Harbor generation/`harbor-audit`/`export-harbor` are required when Harbor is
selected and optional otherwise. Internal score parity never replaces actual
SForge Work/Judge runtime evidence. If scored runtime or semantics change,
retain the old results as historical and remeasure the affected task identity.

The current handoff is a file handoff: the selected format's English package
with its English README/materials, plus a separate English QC report. Two
Chinese translations are kept in an isolated owner-review directory, excluded
from customer bytes. This is a delivery-channel decision relative to
Meta's Smoldata/S3 workflow, not an assertion of platform preflight or
acceptance. Meta V3.0's normal CLI validates Harbor and its special non-Harbor
sample route is not generally intended for RL tasks; no upload/finalize step
is performed without a subsequent explicit recipient agreement.

## Decisions awaiting discussion

- If the recipient later requires an upload despite the current ZIP/report
  handoff decision, confirm a SForge-compatible Smoldata/S3 arrangement first.
- Initial-batch no-redelivery rule versus Meta's general redelivery support.
- Reconciliation of the owner's previously excluded source/dedup/authorization
  checks with EdgeBench V1 section 5 if the owner intends to restore them.
  Until a changed instruction is confirmed, retain the explicit prior exclusions
  as `not_required_by_owner`, not source reviews claimed complete.
- Stable customer task identity versus internal scored revision identities.
- Any remaining Meta clauses whose scope differs from EdgeBench's continuous
  rewards, mandatory architecture support and snapshot grading.

Pending rows are questions, not newly authorized work or claimed compliance.

## Applicable requirements mapped to the reusable Skill

| Requirement | Current treatment |
|---|---|
| Meta V3.0 Harbor and unpacked tasks; EdgeBench V1 §§1–2 | Apply Harbor packaging and its gates when the user selects Harbor; apply the SForge mapping and `sforge-audit`/`package-samples` when `sforge-only` is selected. |
| Meta V3 name `org/name` or bare name, snake/kebab case, combined 255-character limit | Map external task/dataset identity with the recipient; do not silently rename frozen SForge IDs or infer sample/licensed from a suffix. |
| Meta V3 network declaration; EdgeBench V1 §2 no-network Work/verifier and environment cleanup | Map the technical requirement to actual SForge Work/Judge contexts and independently executed probes. `internet:false` alone is not a measured isolation result. |
| Work/Judge image construction | The owner permits public build-time access for declared pinned dependencies and digest-pinned bases. Deliver Dockerfiles and contexts, not offline OCI images or wheels. Actual scored Work/Judge execution still needs no-network isolation and reproducible build receipts. |
| Meta V3 three reward types; Terminal-Bench binary-only note | EdgeBench V1 §4 requires `bounded_continuous`, published raw/normalized metrics and complete formula/constants. Do not import the Terminal-Bench binary rule. |
| Meta V3 ARM optional/encouraged | EdgeBench V1 §6 is stricter: both builds and at least one cross-architecture score required. ARM performs other applicable checks except duplicate frontier rollout and Q1–Q7 main review. |
| EdgeBench V1 §3.7 duration | At least 3h; 12h preferred. Declare attainable upper and its measured basis; no wall-clock reward gate. |
| EdgeBench V1 §§3.1–3.6 snapshot/no state/repeats/timing/feedback | Current SForge snapshot contract, actual NOP/interior/reference scoring and five fixed-reference evaluations with spread/headroom; startup/transfer-inclusive timing remains required. |
| EdgeBench V1 §3.8 oracle/open-solution and NOP/batch/three-trial mean | Delivered executable NOP and runnable oracle or explicit open-solution; scored calibration, batch scan and three comparable SForge runs remain actual process evidence, not static file presence. |
| Meta V3 automatic Oracle smoke test for binary tasks | It is a platform workflow, not EdgeBench reference evidence. Its being non-blocking does not waive EdgeBench's own NOP/oracle/repeat requirements. |
| EdgeBench V1 §5 sources/dedup/rights | Explicit previous owner exclusions retained; do not reintroduce them without confirmation. Actual input bytes still pinned for reproducibility. |
| EdgeBench V1 §6 manifests/GPU/resource envelope/reproducible contexts | Actual SForge native role/context measurements, pinned multiarch base, checked GPU envelope where used, external resource agreement and independently reviewed rebuild receipts. |
| EdgeBench V1 §1 SHA-256 for every data file | Export hashes all files except the hash manifest itself and verifies copied task identity and executable modes. |
| Q1–Q7 main review and same-family two hardenings | Current owner production requirements; separate from task Reward. Removed Q8 and SForge dual entry do not return as new rubric items. |
| Smoldata pilot/preflight/finalize, external offer/dataset routing and S3 fallback | The selected package is still local handoff unless the recipient requests the platform flow; do not claim upload/finalization or acceptance. |
| Meta generic redelivery versus V1 no-redelivery first batch | Expose conflict; no replacement/finalization operation is run by this pipeline without an explicit owner decision. |

Old `rl-task-data-file-specification.md` and Topify DOCX are background and
cross-checks. They do not tighten V3's relaxed naming or replace current V1
with its archived initial text. The external README guide governs writing
where compatible; it cannot manufacture missing measurements, upper/lower
models or a Q1–Q8 quality total after the owner removed Q8.
