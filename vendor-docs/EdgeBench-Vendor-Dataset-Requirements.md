# EdgeBench Vendor Dataset Requirements

# Requirement — V1

Sep 14, 2026

What a vendor must ship for the first 100\-task batch\. These are delivery requirements rather than commercial terms, and they need to be agreed *before* the batch is produced: most of what follows is decided at image\-build time and cannot be retrofitted onto tasks that already exist\.

Tasks are delivered in **Harbor format**, per the Meta Vendor Data Delivery Spec\. That is the format to build against\.

Throughout, "EdgeBench" refers to the published benchmark and its SForge harness \(ByteDance\-Seed/EdgeBench, Apache\-2\.0\)\. EdgeBench is our **design reference**, not a second format: where a requirement below exists because EdgeBench works that way, we say so, and the public 51 tasks show what it looks like in practice\. Expressing those requirements is a matter of the right fields in task\.toml, not a different packaging\.

## What changed since V0

V1 supersedes V0\. Every change below came out of reviewing four completed vendor deliveries against V0: the requirements that were met on paper but not in substance, and the failures that only appeared once something was run\. Changed and new text is highlighted like this throughout the document\. 

## 0\. Batch shape

100 tasks, delivered in whatever increments suit your production schedule\.

## 1\. Delivery mechanics

Follow the [Meta Vendor Data Delivery Spec](https://docs.google.com/document/d/1t1rWM0HeDN0ipI7ScjM34Aqd7jmCUC4SlWPEAmgPhkw/edit)\. It governs S3 bucket setup, keypath layout, dataset and task naming, how a redelivery is versioned, and the per\-delivery notification\. Nothing here overrides it and none of it is restated below\. Note that this batch is a purchase, so it lands under licensed/ rather than sample/\.

**One addition beyond the spec: publish a sha256 for every data file\.** A truncated data file is invisible until a task mysteriously fails to grade, which is an expensive way to find out\.

**No redelivery in this round\.** For the first batch we reject a task outright when the data quality is not good, rather than sending it back for a fix\. This keeps the round fast\. We will tell you what failed so it can inform later batches, but there is no fix\-and\-resubmit cycle inside this round\.

## 2\. Environment requirements: separate agent and verifier

**This is the single hardest requirement, and it is what makes section 3 possible\.**

Every task ships two environments:

- an **agent** \(work\) environment the model works in, and

- a **verifier** \(judge\) environment that grades a submitted snapshot and that the agent never enters\.

**The target shape is Terminal\-Bench 4\.0's separate\-verifier mode\.** Points 1 and 2 below *are* that mode, so if you already build tasks for Terminal\-Bench 4\.0 under the Harbor framework they should already hold\. The other five are ours and do not follow from it: points 3 and 7 are additions, and points 4 to 6 are how we need the delivery packaged and run\. Every task in this batch goes through a single path on our side, so all seven are requirements rather than a menu\.

Concretely, per task:

1. the task declares a separate verifier environment, with environment\_mode set to separate;

2. the task ships a real verifier image definition of its own\. Declaring "separate" and then shipping no verifier build is a rejection, not a variant;

3. **the two build contexts are siblings\.** The verifier's build context must not be the agent's, and must not be a subdirectory of the agent's\. Two directories at the same level, neither containing the other\. Isolation that rests on one \.dockerignore line, or on file permissions inside a single shared context, is not isolation we can establish by reading the delivery — and a harness that runs the agent as root defeats the permission version of it outright;

4. **Dockerfiles and build contexts are required\.** A prebuilt OCI image layout is a welcome addition and we will run from it where it is present and sound, but never as a substitute for source\. We reserve the right to build from source and compare\. Both artifacts already exist on your side, so shipping both costs you nothing beyond the upload;

5. network access is disabled for the **agent** and **verifier** scopes: the agent must not reach the network while working, and the verifier must not reach it while grading\. Declare all three scopes in the Harbor network\-policy format the spec specifies\. The **environment** scope may be public where the image build needs to fetch packages; state why in open\_internet\_justification\. The running container must make no network calls regardless of the build\-time setting, and §6's digest pinning is what makes a network\-using build reproducible;

6. **the verifier is stateless and invoked once per submission\. Do not build a long\-lived judge service\.** Our harness creates the verifier environment, runs the grading command inside it, and destroys it, once for each submission\. A judge that must stay up for the duration of a run, that the agent holds a network route to, or that carries state from one grading to the next, is neither required nor preferred\. Where a submission needs rate limiting or its feedback redacted, that belongs on our side of the boundary, not inside a service the agent can reach;

7. **harden the verifier's own environment\.** Before the grading command runs, reset PATH to a known value and unset PYTHONPATH, PYTHONHOME, PYTHONSTARTUP and LD\_PRELOAD\. An agent can plant a shim on any of these inside its own environment; if the submitted snapshot carries it across, it can shadow the interpreter that scores it\.

This is how EdgeBench itself is constructed, and the reasons are its reasons:

- **Isolation is created at build time, not enforced at run time\.** In EdgeBench, every task JSON defines a two\-container setup, and the split is made by the build commands themselves: the work image clones the project, *deletes the test files*, and re\-initialises git history; the judge image clones the same project and *keeps the full suite*\. The agent cannot see the tests because they were never put in its image\.

- **Iterative evaluation depends on it, in our harness and not only in EdgeBench's\.** Our harness grades repeatedly across the run: on each submission it leases a fresh verifier environment, runs the grading command in it, and tears it down, and it also grades passively on a fixed interval\. A task with no separate verifier image has nothing to lease, so there is nothing to submit to and the run collapses to a single grade at the end\.

- **Network isolation assumes it\.** EdgeBench runs the work container with an outbound allowlist limited to the judge endpoint and the model API\. That boundary only means something if the grader is on the other side of it\.

- **It is the shape of the published set\.** All 51 public tasks ship as an image pair, edgebench\.work\.\<task\_id\> and edgebench\.judge\.\<task\_id\>\. A single\-image task is not an EdgeBench task in a stricter sense than style: none of the harness behaviour above applies to it\.

## 3\. Continual\-learning requirements

EdgeBench measures how an agent improves over a long horizon, not one\-shot accuracy\. Its headline result is a score\-versus\-interaction\-time curve, and its scoring rule is the **best result across all submissions** in a run, not the last one\. A task earns its place only if that curve can exist\.

Separate environments \(section 2\) are **necessary but not sufficient**\. Five properties, each of which the task must have and the delivery must state\. Where to spend your effort: **3\.3 our harness supplies for you**, 3\.1 and 3\.4 are shared between us, and **3\.2 and 3\.5 rest entirely on your task** — nothing on our side can detect or repair a failure of either\.

|**\#**|**Property**|**What the delivery must guarantee**|**Why it is required**|
|---|---|---|---|
|3\.1|**Snapshottable submission**|The score is fully determined by a well\-defined set of files, declared as submit paths or an artifacts directory\. Nothing about the score may depend on live process state inside the agent environment\.|Every submission is graded from an archive of those paths\. If two archives of different work can score the same, or the same archive can score differently, "best across submissions" is not a meaningful quantity\.|
|3\.2|**Repeatable grading of an immutable snapshot**|Grading the same snapshot twice returns the same score\. Grading several different snapshots during one run does not let them interfere\. Seeds pinned, no wall\-clock or network dependence\. Demonstrated per task, not asserted: see 3\.6\.|A run produces many graded points, and the curve is built from them\. Non\-determinism turns score movement between submissions into noise that is indistinguishable from learning\.|
|3\.3|**Verifier startable from scratch, per submission**|Our harness already provides the isolation here: it builds a private verifier environment for each submission and destroys it afterwards, so no state can carry from one grading to the next\. What we need from you is that standing it up is **fast** — no warm\-up, no large fixture to stage, no lengthy install at container start\.|Because the isolation is automatic, this is not about contamination\. It is about cost: **nothing bounds verifier start\-up**, and every second of it is charged to the agent's interaction time, on every submission\. A verifier that takes four minutes to come up costs four minutes each time the agent submits\.|
|3\.4|**Declared and bounded grading wall\-clock**|A per\-task verifier timeout, plus your *measured* typical grading time — measured **end to end**, covering verifier start\-up and staging the submission into it, not only the checks themselves\.|Grading time is subtracted from interaction time and sets how many submissions fit in a run\. A task whose verifier takes an hour supports very few submissions in a twelve\-hour budget, and we need to know that before the run rather than after\. The timeout you declare bounds only the scoring command; start\-up and staging sit outside it and are not capped, so that number on its own understates what a submission costs\.|
|3\.5|**Structured verifier output**|Not a single scalar\. At minimum: an overall score, a validity flag, and a list of **named checks with pass/fail**\. Tell us how your verifier's output is meant to be read, so we can parse it into those fields\.|EdgeBench returns pass rate, score, *failed item names* and a summary to the agent after each submission, and that diagnostic content is what the agent improves against\. A verifier that emits only a number leaves the agent with no information about what to fix, and the curve flattens for a reason that has nothing to do with model capability\. On our side this is also a hard requirement rather than a quality preference: the score must arrive paired with a validity flag, and output we cannot parse into that shape **fails to grade at all** rather than scoring low\.|

### 3\.6 Determinism evidence

Property 3\.2 is the one most likely to be true in intention and false in practice, so we ask for a measurement rather than a statement\.

**Per task: grade one fixed snapshot five times and report the five scores\.** Use your reference solution's output as the snapshot\. Report the spread \(max minus min\) alongside the task's baseline and ceiling\.

The acceptance test is relative, not absolute: **the grading spread must be small against the task's own headroom\.** A spread of 0\.002 is negligible on a task with 0\.60 of headroom and disqualifying on one with 0\.05, because in the second case re\-grading an unchanged submission moves it several percent of the way up the reward range\. Tasks whose metric is intrinsically noisy are still welcome; they just need enough headroom that the noise does not dominate\.

### 3\.7 What makes a curve possible at all

Beyond the mechanics, the task has to be learnable over time\. We check all four at intake:

- **Long declared agent budget\.** Hours, not minutes: a sub\-hour budget cannot produce a curve and is rejected\. **3 hours is the minimum we accept\.** EdgeBench sustains 12 hours of continuous operation and that remains our preference, since more interaction time means more points on the curve\. Tell us your reference solution's typical convergence time, so we can judge whether the cap is generous or binding\.

- **Continuous partial credit\.** A binary pass/fail task has nothing to plot between 0 and 1\.

- **An agent\-visible local feedback loop\.** A development split plus a scorer the agent can run itself, inside its own environment\. Submissions are rate\-limited and graded remotely; without a local signal the agent has no way to choose what to try *between* submissions, and a long budget buys nothing\.

- **An attainable ceiling, and state how you established it\.** The ceiling is the score a strong solution can actually reach, not the metric's theoretical maximum\. A ceiling of 1\.0 set because accuracy cannot exceed 1\.0 is not a ceiling, and it collapses the usable reward range on any task whose baseline is already high: a baseline of 0\.95 against a ceiling of 1\.0 leaves 0\.05 of headroom, which is often within the task's own measurement noise \(see 3\.6\)\. Tell us where the number came from, whether that is a human expert result, an ensemble, a published state of the art, or a saturation estimate\.

- **Say where your reference solution sits on the reward curve\.** If the normalisation is anchored so that the reference run scores a fixed value, state that value\. It is the single most useful calibration point we have, and it tells us whether the range above the reference is real headroom or decoration\. The reference must be **runnable**, not only described: ship the artifact that produces the number, under 3\.8\.

### 3\.8 Reference runs

Three artifacts, produced in a single pass on your side: a nop, an oracle, and a reference score\. Together they are the only evidence that a task's reward means what its metadata says it means, and not one of them can be recovered from a static read of the delivery\. This subsection replaces V0's 3\.8, which asked only for the last and hardest of the three; the two tiers below make the cheap part mandatory and keep the expensive part as the target\.

**Required, per task:**

- **A nop script, and the result of running it\.** A submission that does nothing must score the task's floor\. Ship the script *and* your own sweep result across the batch — we will not have the capacity to run this ourselves on every delivery\. This is the cheapest artifact in the set and the one with the highest hit rate: in the one batch anyone has swept this way, 21% of tasks paid a nonzero reward for an empty submission\. A task that pays for nothing teaches a policy that submitting nothing is worth something, and no static check finds it\.

- **A runnable oracle, or an explicit open\-solution declaration in metadata\.** The oracle shows the task is solvable and that the reward actually reaches its declared top\. Where a task has no definable oracle — an open research goal, or a reference the agent is meant to reimplement rather than copy — declare it open\-solution in the task metadata\. That is a perfectly acceptable answer\. What is not acceptable is a reference solution file that does nothing: see 3\.9\.

- **A score table** giving, per task, the oracle value, the nop value, and the mean score of at least one frontier model over **three or more trials**\. Three numbers per task\. This is what lets us confirm that our harness reproduces your figures, and it is what tells us whether the declared reward endpoints are the true endpoints or just the metric's bounds\.

- **How many submissions your reference run made\.** A reference that submitted a hundred times across twelve hours and one that submitted once can report the same score and mean entirely different things\. Without this number a reference score is not comparable with anything we produce\.

**Preferred:** the same model scored at **three or more interaction checkpoints**, for example 2 h, 6 h and 12 h\. This is exactly the per\-task data the published EdgeBench appendix reports for all 134 of its tasks, and it is the only pre\-delivery evidence that a task genuinely improves with time rather than being flat, saturated, or impossible\. It subsumes the required tier: if you produce the curve, the required table falls out of it\.

### 3\.9 Explicitly disallowed

- **Wall\-clock or minimum\-duration gates\.** A reward forced to zero below some elapsed time is satisfiable by sleeping, and capable models find that immediately\. Measure the work, not the clock\.

- **Undeclared grading paths\.** If any task in the batch is graded by a mechanism other than the one declared for the batch, name it up front\.

- **A no\-op reference solution\.** A shipped solve\.sh that does nothing is worse than shipping no file at all: it reads as oracle\-bearing to every check that does not open it, and it is not caught until someone runs it\. If a task has no oracle, declare it open\-solution under 3\.8 instead\.

## 4\. Reward requirements

- **Set reward\_type = "bounded\_continuous" in task\.toml\.** The spec allows binary, bounded\_continuous and unbounded\_continuous, and defaults to binary when the field is omitted\. For this batch neither of the other two works: binary collapses the task to pass/fail so there is no curve to measure, and unbounded\_continuous is not comparable across tasks, so it cannot be aggregated into one\. Omitting the field is the common failure here, because the default is silent\.

- **The scoring formula and every one of its constants belong in machine\-readable metadata**, not only inside verifier source\. A constant that exists solely as a literal in code cannot be validated, versioned, or reasoned about without reading the verifier\.

- **Publish the normalised score directly**, alongside the raw metric\. A custom normalisation curve we have to reimplement on our side is a source of divergence between your number and ours\.

- **Declare the score direction** \(higher\- or lower\-is\-better\), and keep the score monotonic: no gains from negative work\.

- **Publish the baseline and ceiling per task, by exactly one mechanism across the batch\.** Two mechanisms in one delivery means anything that reads one of them reports the other half of the batch as unscored\.

## 5\. Provenance and non\-overlap

- **Per task: source repository URL, upstream project name, and any public dataset used\.** Task names are not enough to establish non\-overlap\. Two tasks built from the same upstream project share its code, its issue history, and whatever solutions exist for it in public training data, whatever they are called\. EdgeBench's own task names make its upstreams visible \(ffmpeg, schemathesis, nlohmann/json, pocketbase, Lua, zstd, OpenTTD, NetHack, Wesnoth, Lean/mathlib, and others\), and a new task built on any of those is a collision even under a different name\. The upstream identifiers are what make the check possible\.

- **Run a decontamination check before delivery, and send us what you found\.** Compare your batch, at the level of upstream repository and dataset rather than task name, against:

    - **EdgeBench** — 134 tasks, of which 51 are released publicly and 83 are held out\. The public task list and its upstreams are in the repository; for the held\-out 83 we can run the comparison on our side from the identifiers above\.

    - **MLS\-Bench**

    - **Terminal\-Bench**

    - **GDPval**

- **Redistribution rights** for every source repository, with the licence and written confirmation that the task can be released to us on those terms\.

- **Category coverage**

EdgeBench spans six capability categories\. Its own mix across all 134 tasks is below, with public tasks named as examples of what each category contains\. Aim for a batch that spreads across these rather than concentrating in one area, and tell us which category each task belongs to\.

|**Category**|**Share**|**What it covers**|**Public examples**|
|---|---|---|---|
|**Systems \& Software Engineering**|26%|Re\-implementing, porting or extending real infrastructure: codecs and compression engines, compilers and language runtimes, network protocol stacks, storage and search systems, and large refactors of existing open\-source codebases\.|ffmpeg\_swscale\_reimplementation, arc\_compiler\_runtime, integer\_compression\_codec, ann\_vector\_search\_qps|
|**Scientific Problems \& ML**|25%|Building, tuning or reproducing a model or solver measured against a quantitative metric: inverse problems, signal processing, property prediction, forecasting, anomaly detection, reinforcement\-learning control\.|graph\_node\_classification, borden\_source\_inversion, dabic\_gravity\_inversion, bipedalwalker\_locomotion\_rl|
|**Professional Knowledge Work**|18%|Domain\-expert deliverables judged on substantive correctness: quantitative finance and actuarial work, compliance and audit, education and assessment, clinical meta\-analysis, structured reporting\.|portfolio\_risk\_calibration, cta\_risk\_budget\_optimization, k12\_math\_recommendation, college\_english\_exam\_bank|
|**Combinatorial Optimization**|12%|Hard search and assignment problems scored on solution quality rather than correctness: routing, packing and nesting, partitioning, graph colouring, and solver construction\.|vehicle\_routing\_time\_windows, jagua\_nesting\_optimization, tree\_block\_partitioning, smt\_solver|
|**Formal Math \& Theorem Proving**|10%|Mechanised proof against a formal target, predominantly Lean and mathlib\.|pfr\_formalization, sphere\_eversion\_formalization, carleson\_formalization, lean\_analysis\_proofs|
|**Interactive Games \& Simulators**|9%|Long\-horizon agents driving a live game or simulator through an API, scored on outcomes achieved over an extended session\.|nethack\_dungeon\_agent, wesnoth\_tactical\_ai, openttd\_transport\_ai, anchorhead\_text\_adventure|

## 6\. Reproducibility and resources

- **Images and Dockerfiles must build for both linux/amd64 and linux/arm64,** demonstrated by a multi\-platform build per task\. **Grading must agree across architectures:** include at least one cross\-architecture grading in the §3\.6 determinism evidence and report the cross\-arch spread against the task's headroom\.

- **If a task pins gpu\_types,** tell us which accelerators it is validated on, and whether grading executes on the GPU\. A task pinned to a single accelerator family is effectively single\-platform in our fleet whatever its image supports, and we need to agree that trade before the batch is built\.

- **Pin base images by digest, to the multi\-architecture manifest list rather than to a platform\-specific image\.** These two requirements collide if you pin the wrong digest: a linux/amd64 image digest resolves on x86 and fails on ARM, which looks like a pinning problem and is actually an architecture one\. Pin the manifest\-list digest and you satisfy both\. A floating tag satisfies neither, since a determinism check today says nothing about the same task six months from now\.

- **If you also ship a prebuilt OCI image layout** \(optional, per §2\), it must be internally consistent: every declaration of a given digest must agree across the index, the manifests and the image configs, and every blob the index references must be present in the layout\. Once a layout is the thing we run, this is the only integrity check available, so it has to hold exactly\. The layout must also carry both architectures, the same requirement as the build above\.

- **Per\-task resource declarations, within a ceiling agreed before the batch is built\.** Memory in particular: a task that requires more than the agreed ceiling cannot be run at all, and that is much cheaper to settle up front than to discover task by task\.

- **Builds reproducible from the shipped context**, with any network dependencies declared\.

# \[Archived\]Initial Round Requirement

Aug 26, 2026

What a vendor must ship for the first 100\-task batch\. These are delivery requirements rather than commercial terms, and they need to be agreed *before* the batch is produced: most of what follows is decided at image\-build time and cannot be retrofitted onto tasks that already exist\.

Tasks are delivered in **Harbor format**, per the Meta Vendor Data Delivery Spec\. That is the format to build against\.

Throughout, "EdgeBench" refers to the published benchmark and its SForge harness \(ByteDance\-Seed/EdgeBench, Apache\-2\.0\)\. EdgeBench is our **design reference**, not a second format: where a requirement below exists because EdgeBench works that way, we say so, and the public 51 tasks show what it looks like in practice\. Expressing those requirements is a matter of the right fields in task\.toml, not a different packaging\.

## 0\. Batch shape

100 tasks, delivered in whatever increments suit your production schedule\.

## 1\. Delivery mechanics

Follow the [Meta Vendor Data Delivery Spec](https://docs.google.com/document/d/1t1rWM0HeDN0ipI7ScjM34Aqd7jmCUC4SlWPEAmgPhkw/edit)\. It governs S3 bucket setup, keypath layout, dataset and task naming, how a redelivery is versioned, and the per\-delivery notification\. Nothing here overrides it and none of it is restated below\. Note that this batch is a purchase, so it lands under licensed/ rather than sample/\.

**One addition beyond the spec: publish a sha256 for every data file\.** A truncated data file is invisible until a task mysteriously fails to grade, which is an expensive way to find out\. 

**No redelivery in this round\.** For the first batch we reject a task outright when the data quality is not good, rather than sending it back for a fix\. This keeps the round fast\. We will tell you what failed so it can inform later batches, but there is no fix\-and\-resubmit cycle inside this round\.

## 2\. Environment requirements: separate agent and verifier

**This is the single hardest requirement, and it is what makes section 3 possible\.**

Every task ships two environments:

- an **agent** \(work\) environment the model works in, and

- a **verifier** \(judge\) environment that grades a submitted snapshot and that the agent never enters\.

Concretely, per task:

1. the task declares a separate verifier environment;

2. the task ships a real verifier image definition of its own\. Declaring "separate" and then shipping no verifier build is a rejection, not a variant;

3. network access is disabled for the **agent** and **verifier** scopes: the agent must not reach the network while working, and the verifier must not reach it while grading\. Declare all three scopes in the Harbor network\-policy format the spec specifies\. The **environment** scope may be public where the image build needs to fetch packages; state why in open\_internet\_justification\. The running container must make no network calls regardless of the build\-time setting, and §6's digest pinning is what makes a network\-using build reproducible\.

This is how EdgeBench itself is constructed, and the reasons are its reasons:

- **Isolation is created at build time, not enforced at run time\.** In EdgeBench, every task JSON defines a two\-container setup, and the split is made by the build commands themselves: the work image clones the project, *deletes the test files*, and re\-initialises git history; the judge image clones the same project and *keeps the full suite*\. The agent cannot see the tests because they were never put in its image\.

- **Iterative evaluation depends on it\.** When the agent submits, the judge server starts a **temporary judge container**, runs the evaluation command inside it, and tears it down\. A task with no separate judge image has no container to start per submission, so there is nothing to submit to and the run collapses to a single shot at the end\.

- **Network isolation assumes it\.** EdgeBench runs the work container with an outbound allowlist limited to the judge endpoint and the model API\. That boundary only means something if the grader is on the other side of it\.

- **It is the shape of the published set\.** All 51 public tasks ship as an image pair, edgebench\.work\.\<task\_id\> and edgebench\.judge\.\<task\_id\>\. A single\-image task is not an EdgeBench task in a stricter sense than style: none of the harness behaviour above applies to it\.

## 3\. Continual\-learning requirements

EdgeBench measures how an agent improves over a long horizon, not one\-shot accuracy\. Its headline result is a score\-versus\-interaction\-time curve, and its scoring rule is the **best result across all submissions** in a run, not the last one\. A task earns its place only if that curve can exist\.

Separate environments \(section 2\) are **necessary but not sufficient**\. Five properties, each of which the task must have and the delivery must state:

|**\#**|**Property**|**What the delivery must guarantee**|**Why it is required**|
|---|---|---|---|
|3\.1|**Snapshottable submission**|The score is fully determined by a well\-defined set of files, declared as submit paths or an artifacts directory\. Nothing about the score may depend on live process state inside the agent environment\.|Every submission is graded from an archive of those paths\. If two archives of different work can score the same, or the same archive can score differently, "best across submissions" is not a meaningful quantity\.|
|3\.2|**Repeatable grading of an immutable snapshot**|Grading the same snapshot twice returns the same score\. Grading several different snapshots during one run does not let them interfere\. Seeds pinned, no wall\-clock or network dependence\. Demonstrated per task, not asserted: see 3\.6\.|A run produces many graded points, and the curve is built from them\. Non\-determinism turns score movement between submissions into noise that is indistinguishable from learning\.|
|3\.3|**Verifier startable from scratch, per submission**|The verifier is self\-contained and cheap enough to stand up repeatedly\. No warm\-up, no shared fixture, no state carried over from a previous grading\.|A fresh judge container is created and destroyed for each submission\. Anything the verifier needs that survives between submissions is state the next submission can be contaminated by\.|
|3\.4|**Declared and bounded grading wall\-clock**|A per\-task verifier timeout, plus your *measured* typical grading time\.|Grading time is subtracted from interaction time and sets how many submissions fit in a run\. A task whose verifier takes an hour supports very few submissions in a twelve\-hour budget, and we need to know that before the run rather than after\.|
|3\.5|**Structured verifier output**|Not a single scalar\. At minimum: an overall score, a validity flag, and a list of **named checks with pass/fail**\.|EdgeBench returns pass rate, score, *failed item names* and a summary to the agent after each submission, and that diagnostic content is what the agent improves against\. A verifier that emits only a number leaves the agent with no information about what to fix, and the curve flattens for a reason that has nothing to do with model capability\.|

### 3\.6 Determinism evidence

Property 3\.2 is the one most likely to be true in intention and false in practice, so we ask for a measurement rather than a statement\.

**Per task: grade one fixed snapshot five times and report the five scores\.** Use your reference solution's output as the snapshot\. Report the spread \(max minus min\) alongside the task's baseline and ceiling\.

The acceptance test is relative, not absolute: **the grading spread must be small against the task's own headroom\.** A spread of 0\.002 is negligible on a task with 0\.60 of headroom and disqualifying on one with 0\.05, because in the second case re\-grading an unchanged submission moves it several percent of the way up the reward range\. Tasks whose metric is intrinsically noisy are still welcome; they just need enough headroom that the noise does not dominate\.

### 3\.7 What makes a curve possible at all

Beyond the mechanics, the task has to be learnable over time\. We check all four at intake:

- **Long declared agent budget\.** Hours, not minutes: a sub\-hour budget cannot produce a curve and is rejected\. **3 hours is the minimum we accept\.** EdgeBench sustains 12 hours of continuous operation and that remains our preference, since more interaction time means more points on the curve\. Tell us your reference solution's typical convergence time, so we can judge whether the cap is generous or binding\.

- **Continuous partial credit\.** A binary pass/fail task has nothing to plot between 0 and 1\.

- **An agent\-visible local feedback loop\.** A development split plus a scorer the agent can run itself, inside its own environment\. Submissions are rate\-limited and graded remotely; without a local signal the agent has no way to choose what to try *between* submissions, and a long budget buys nothing\.

- **An attainable ceiling, and state how you established it\.** The ceiling is the score a strong solution can actually reach, not the metric's theoretical maximum\. A ceiling of 1\.0 set because accuracy cannot exceed 1\.0 is not a ceiling, and it collapses the usable reward range on any task whose baseline is already high: a baseline of 0\.95 against a ceiling of 1\.0 leaves 0\.05 of headroom, which is often within the task's own measurement noise \(see 3\.6\)\. Tell us where the number came from, whether that is a human expert result, an ensemble, a published state of the art, or a saturation estimate\.

- **Say where your reference solution sits on the reward curve\.** If the normalisation is anchored so that the reference run scores a fixed value, state that value\. It is the single most useful calibration point we have, and it tells us whether the range above the reference is real headroom or decoration\.

### 3\.8 Time\-resolved reference curve

**Per task, the score of at least one frontier model at three or more interaction checkpoints, for example 2 h, 6 h and 12 h\.**

This is the most valuable single addition to the delivery\. It is inexpensive for you, since you already run reference solutions; it is exactly the per\-task data the published EdgeBench appendix reports for all 134 of its tasks; and it is the only pre\-delivery evidence that a task actually improves with time rather than being flat, saturated, or impossible\.

### 3\.9 Explicitly disallowed

- **Wall\-clock or minimum\-duration gates\.** A reward forced to zero below some elapsed time is satisfiable by sleeping, and capable models find that immediately\. Measure the work, not the clock\.

- **Undeclared grading paths\.** If any task in the batch is graded by a mechanism other than the one declared for the batch, name it up front\.

## 4\. Reward requirements

- **Set reward\_type = "bounded\_continuous" in task\.toml\.** The spec allows binary, bounded\_continuous and unbounded\_continuous, and defaults to binary when the field is omitted\. For this batch neither of the other two works: binary collapses the task to pass/fail so there is no curve to measure, and unbounded\_continuous is not comparable across tasks, so it cannot be aggregated into one\. Omitting the field is the common failure here, because the default is silent\.

- **The scoring formula and every one of its constants belong in machine\-readable metadata**, not only inside verifier source\. A constant that exists solely as a literal in code cannot be validated, versioned, or reasoned about without reading the verifier\.

- **Publish the normalised score directly**, alongside the raw metric\. A custom normalisation curve we have to reimplement on our side is a source of divergence between your number and ours\.

- **Declare the score direction** \(higher\- or lower\-is\-better\), and keep the score monotonic: no gains from negative work\.

- **Publish the baseline and ceiling per task, by exactly one mechanism across the batch\.** Two mechanisms in one delivery means anything that reads one of them reports the other half of the batch as unscored\.

## 5\. Provenance and non\-overlap

- **Per task: source repository URL, upstream project name, and any public dataset used\.** Task names are not enough to establish non\-overlap\. Two tasks built from the same upstream project share its code, its issue history, and whatever solutions exist for it in public training data, whatever they are called\. EdgeBench's own task names make its upstreams visible \(ffmpeg, schemathesis, nlohmann/json, pocketbase, Lua, zstd, OpenTTD, NetHack, Wesnoth, Lean/mathlib, and others\), and a new task built on any of those is a collision even under a different name\. The upstream identifiers are what make the check possible\.

- **Run a decontamination check before delivery, and send us what you found\.** Compare your batch, at the level of upstream repository and dataset rather than task name, against:

    - **EdgeBench** — 134 tasks, of which 51 are released publicly and 83 are held out\. The public task list and its upstreams are in the repository; for the held\-out 83 we can run the comparison on our side from the identifiers above\.

    - **MLS\-Bench**

    - **Terminal\-Bench**

    - **GDPval**

- **Redistribution rights** for every source repository, with the licence and written confirmation that the task can be released to us on those terms\.

- **Category coverage**

EdgeBench spans six capability categories\. Its own mix across all 134 tasks is below, with public tasks named as examples of what each category contains\. Aim for a batch that spreads across these rather than concentrating in one area, and tell us which category each task belongs to\.

|**Category**|**Share**|**What it covers**|**Public examples**|
|---|---|---|---|
|**Systems \& Software Engineering**|26%|Re\-implementing, porting or extending real infrastructure: codecs and compression engines, compilers and language runtimes, network protocol stacks, storage and search systems, and large refactors of existing open\-source codebases\.|ffmpeg\_swscale\_reimplementation, arc\_compiler\_runtime, integer\_compression\_codec, ann\_vector\_search\_qps|
|**Scientific Problems \& ML**|25%|Building, tuning or reproducing a model or solver measured against a quantitative metric: inverse problems, signal processing, property prediction, forecasting, anomaly detection, reinforcement\-learning control\.|graph\_node\_classification, borden\_source\_inversion, dabic\_gravity\_inversion, bipedalwalker\_locomotion\_rl|
|**Professional Knowledge Work**|18%|Domain\-expert deliverables judged on substantive correctness: quantitative finance and actuarial work, compliance and audit, education and assessment, clinical meta\-analysis, structured reporting\.|portfolio\_risk\_calibration, cta\_risk\_budget\_optimization, k12\_math\_recommendation, college\_english\_exam\_bank|
|**Combinatorial Optimization**|12%|Hard search and assignment problems scored on solution quality rather than correctness: routing, packing and nesting, partitioning, graph colouring, and solver construction\.|vehicle\_routing\_time\_windows, jagua\_nesting\_optimization, tree\_block\_partitioning, smt\_solver|
|**Formal Math \& Theorem Proving**|10%|Mechanised proof against a formal target, predominantly Lean and mathlib\.|pfr\_formalization, sphere\_eversion\_formalization, carleson\_formalization, lean\_analysis\_proofs|
|**Interactive Games \& Simulators**|9%|Long\-horizon agents driving a live game or simulator through an API, scored on outcomes achieved over an extended session\.|nethack\_dungeon\_agent, wesnoth\_tactical\_ai, openttd\_transport\_ai, anchorhead\_text\_adventure|

## 6\. Reproducibility and resources

- **Images and Dockerfiles must build for both linux/amd64 and linux/arm64,** demonstrated by a multi\-platform build per task\. **Grading must agree across architectures:** include at least one cross\-architecture grading in the §3\.6 determinism evidence and report the cross\-arch spread against the task's headroom\.

- **If a task pins gpu\_types,** tell us which accelerators it is validated on, and whether grading executes on the GPU\. A task pinned to a single accelerator family is effectively single\-platform in our fleet whatever its image supports, and we need to agree that trade before the batch is built\.

- **Pin base images by digest, to the multi\-architecture manifest list rather than to a platform\-specific image\.** These two requirements collide if you pin the wrong digest: a linux/amd64 image digest resolves on x86 and fails on ARM, which looks like a pinning problem and is actually an architecture one\. Pin the manifest\-list digest and you satisfy both\. A floating tag satisfies neither, since a determinism check today says nothing about the same task six months from now\.

- **Per\-task resource declarations, within a ceiling agreed before the batch is built\.** Memory in particular: a task that requires more than the agreed ceiling cannot be run at all, and that is much cheaper to settle up front than to discover task by task\.

- **Builds reproducible from the shipped context**, with any network dependencies declared\.

