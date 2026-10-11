# Meta Vendor Data Spec V3\.0

Meta Vendor Data Delivery Spec V3\.0

# Motivation

All tasks are ingested into a Meta system which tracks the metadata and runs validations on tasks to ensure they are high quality\. In order to be successfully ingested, we ask that datasets have a specific format\.

## Delivering Tasks through Smoldata

|Benefits of smoldata:<br>1. You can validate and fix structural data issues before delivery using smoldata’s cli\. This reduces rejection risk by making the data higher quality upfront<br>2. Datasets are attached to listings \(offers or asks\) which contain pricing and volume info\. This makes the data procurable|
|---|

## Sign up for Smoldata

Register at [smoldata\.ai](http://smoldata.ai) for an account\. If your company has existing accounts a supervisor can approve your signup\. If you are the first member, you will go through onboarding\.

Detailed instructions: [Meta Vendor Data Delivery Spec V3\.0](https://docs.google.com/document/d/1t1rWM0HeDN0ipI7ScjM34Aqd7jmCUC4SlWPEAmgPhkw/edit?tab=t.mhkrvcgncoz3#heading=h.sbfvzekhlbxx) \- “Smoldata Onboarding” tab\.

## Data Delivery with Smoldata

In all cases you can ask your agent to do what you want and the agent will walk you through the process\. The following documents the steps under the hood\.

|Creating Licensed Datasets|1. Find an existing dataset you want to deliver to\. If none exists, create a new Offer with the previously agreed\-to terms plus dataset\.<br>    2. Use the cli to create a new delivery into this dataset \(see “Delivering Data” below\)\.|
|---|---|
|Creating Sample Datasets<br>\(Off The Shelf\)|1. Create a new Offer \(listing\) with **price** **and** **volume** plus other metadata about the listing\. This helps our team discover your offer\.<br>    2. Create a new dataset\. Pick a data format \(e\.g\. harbor\-v2\) and reward type \(e\.g\. binary or bounded\_continuous\)\. See below for other task format requirements\.<br>    3. Deliver data into this dataset \(see “Delivering Data” below\)\.|
|Delivering Data|1. Your agent will create a new **draft delivery**\.<br>    2. Your agent is told to upload one task to start \(much faster to prevalidate one before doing the rest\)\.<br>    3. The **preflight check** will validate structural requirements of the task \(e\.g\. task\.toml format\)\.<br>    4. Your agent will poll and wait for smoke test validations to finish\. Right now this is the Oracle check\. Your agent should fix any issues\.<br>        1. Note: smoke tests do not block finalization<br>    5. Upon success, your agent will finalize the delivery\. Any future changes will require re\-delivery\.<br>Once one passes, your agent will upload the rest and run the process on the rest of the delivery\.|
|Re\-delivering Data|In cases of needing to re\-delivery data \(fix issues of already finalized data\), ask your agent to re\-deliver already finalized data\.<br>1. Your agent will create a new delivery batch\.<br>2. Make sure that the task ids are the same as the data meant to be re\-delivered\.<br>3. Data is delivered according to “Delivering Data”|
|Issues with CLI|1. Ask your agent to file a bug\.<br>    2. If you are blocked by this, share a link of this bug in vendor chat and tag Chris Li \(chli\) for faster resolution\.|

# RL Task Format \& Requirements

- We would want our tasks runnable on **both x86 and ARM**, so we need the cross\-platform compatible **images and DOCKERFILEs** that support both x86 and ARM\. 

    - This is highly encouraged and tasks which support both have more use to us 

- Task must be in harbor format and all tasks should be unzipped 

# Task\.toml 

Must  include \(not an exclusive list\):

- Task name matching directory name \(note: name refers to the part after / in harbor docs\. A bare name without “org/” is also accepted for backwards compatibility\)

    - name = "\<org\>/\<name\>"

- Internet access

- Reward type

- \[optional\] GPU type

Follow  [https://www\.harborframework\.com/docs/tasks](https://www.harborframework.com/docs/tasks)  Configuration \& Metadata for the task\.toml ​​

## Internet access: Declare whether your task needs internet for environment, agent, verifier

**Instruction: **

Set the internet access information with the same format as the harbor[https://www\.harborframework\.com/docs/tasks/network\-policy](https://www.harborframework.com/docs/tasks/network-policy) in task\.toml

**Example:**

![image6\.png](图片和附件/image6.png)

## Reward type: Declare how your task is scored

**Instruction: **

Set reward\_type to one of binary \| bounded\_continuous \| unbounded\_continuous\. 

**Note: TerminalBench data has to be binary reward**

**Example:**

\# task\.toml

\[metadata\]

category = "Science"

reward\_type = "binary" \| "unbounded\_continuous" \| "bounded\_continuous"



**Explanation: **Almost all of our tasks so far have used binary rewards\. But in order to add increasingly complex tasks, we need to be able to describe more nuanced rewards\. This field drives how results are scored and surfaced in our dashboards\.

Continuous \(i\.e\. non\-binary\) tasks show their real float score \+ per\-step pass rates instead of being collapsed to pass/fail\. Some tasks \(especially in the ML, LLM, and Intelligence tracks\) need non\-binary rewards to express the quality of the solution\. For example, a handwriting recognition ML model could be scored on the percentage of characters it successfully identifies, rather than be flattened to pass/fail\.

We are differentiating bounded and unbounded continuous results to enable specific types of problems as well \(e\.g\. a loss function does not have a bounded result\)\. The values are defined as follows:

binary: 0 \| 1 \(e\.g\. 0\) 

unbounded\_continuous: \-Infinity\.\.Infinity \(e\.g\. 340\.3\) 

bounded\_continuous: 0…1 \(e\.g\. 0\.4\)



This field defaults to binary when omitted, so existing pass/fail tasks are unaffected\.

## GPU tasks

If it’s a GPU task, you can  include this information:

\# task\.toml

\[environment\]

gpus = 1

gpu\_types = \["H100"\]



# Additional Requirements

For ML tasks: [EdgeBench Vendor Dataset Initial Requirement](https://docs.google.com/document/d/1HlROJFFTgtFA1j9I_bKDd68HUdYGCfRHZaVBAXpdHC8/edit?tab=t.4gb76hyj2aan#heading=h.1n6r4eepf8r2)

- V2\.1 Added delivery post requirement

- V2\.2 added cross\-platform compatible image, dockerfile requirement 

- V2\.3 Added  \`task\.toml\` with internet access/network policy requirements

- V2\.4

    - Added sample data delivery and s3 bucket create

    - **TerminalBench data has to be binary reward**

    - **Add additional guidelines on ML tasks **[**EdgeBench Vendor Dataset Initial Requirement**](https://docs.google.com/document/d/1HlROJFFTgtFA1j9I_bKDd68HUdYGCfRHZaVBAXpdHC8/edit?tab=t.4gb76hyj2aan#heading=h.1n6r4eepf8r2)

- V3\.0 

    - Proposed: No\-network will be the default internet policy for tasks

    - added the requirement ‘all tasks should be unzipped’

    - for gpu tasks, add an optional gpu type

    - Relax the naming by using snake\_case or kebab\-case

    - Clarified that the task format can be \<org\>/\<task\-name\>

    - ARM support is optional but encouraged

    - Smoldata will now validate harbor format

# Smoldata vendor onboarding

Set up once, then drive your coding agent to list, deliver, and fix data\.

*Last updated September 26, 2026\.*

|**How this guide works:** you tell your coding agent what you want; the agent runs the Smoldata vendor CLI\. The CLI ships its own instructions \(commands, task format, limits, error fixes\), and the agent reads and updates them every session, so this guide does not repeat them\. It covers the concepts you need, the decisions that are yours, and what to ask for\.|
|---|

## 1\. Get set up \(once\)

1. **Create your account** at [https://smoldata\.ai/sign\-up](https://smoldata.ai/sign-up) with your invited work email\. If you see **Your account is pending approval**, copy the approval request on that page and send it to whoever the page names \(a supervisor at your company, or your Smoldata contact\)\.

2. **Finish the first\-time check\-in** at [https://smoldata\.ai/vendor](https://smoldata.ai/vendor): confirm your team contact email, optionally connect a Google Chat space for notifications, and acknowledge the orientation\.

3. **Connect your coding agent:** open **Connect an Agent**, select **Generate setup prompt**, and paste the *whole* prompt into a coding agent with terminal access \(for example Claude Code or Codex\)\. The agent installs and authenticates the CLI itself; you never create or handle an API key\.

4. Wait for the agent to report that it is connected to https://smoldata\.ai with your Vendor ID \(smol\-vnd\-…\)\.

![image7\.png](图片和附件/image7.png)

*Sign up with your invited work email\.*

![image3\.png](图片和附件/image3.png)

*The first\-time check\-in\.*

![image2\.png](图片和附件/image2.png)

*Connect an Agent → Generate setup prompt\. Paste the whole prompt into your agent \(link redacted here\)\.*

|**Keep the setup prompt private\.** It is a single\-use link that expires after 30 minutes; paste it only into the intended agent\. If it expires, generate a new one\. Generating a new prompt cancels your previous unused one, so paste each prompt into its agent before generating the next\. To disconnect a machine, select **Revoke** on **Connect an Agent**\.|
|---|

## 2\. How Smoldata organizes your data

Four objects matter\. Your agent will use these names, and every ID it reports opens in the browser at https://smoldata\.ai/id/\<id\>\.

|**Object**|**What it is**|**Key rules**|
|---|---|---|
|**Offer** \(listing\)|Your commercial listing for one dataset: price, volume, and a description of the data\.|Unit price and total volume are required before Smoldata can review deliveries\. More metadata = better discovery\. Drafts stay unpublished until you publish them; only your team and Smoldata staff can see them\. Other vendors never see your offers\.|
|**Dataset** \(smol\-col\-…\)|The container your tasks are delivered into\. Belongs to one offer\.|Either **sample** \(representative data for evaluation\) or **licensed** \(data delivered under contract\)\. The name and sample/licensed choice are permanent\. The evaluation type \(binary, bounded\_continuous, unbounded\_continuous\) is also set at creation and can only be corrected while the delivery is still a draft, so choose carefully\.|
|**Delivery** \(batch, smol\-dlb\-…\)|One dated upload of tasks into a dataset\.|Starts as an editable **Draft**\. **Finalizing** makes it **Ready** and immutable; fixes after that require a re\-delivery\.|
|**Task** \(smol\-tsk\-…\)|One unit of data: one directory in Harbor task format \(task\.toml, instruction\.md, tests/, …\)\.|Checked automatically on upload \(Vendor preflight, plus Oracle for binary tasks\)\.|

In short: **Offer → Dataset\(s\) → Deliveries → Tasks\.** An offer typically has a sample dataset, and licensed datasets once purchased\.

## 3\. Driving your agent

- **Just describe the outcome\.** The agent updates its CLI at the start of each session, reads the current instructions, and walks you through the steps\.

- **You make the decisions; the agent asks for them\.** Expect to be asked: which offer/dataset the data goes into \(existing or new\), sample vs licensed, evaluation type, the source directory, the delivery date \(defaults to today\), and commercials for a new offer\. Never let the agent guess these\.

- **Say whether it may fix your files\.** The agent asks before repairing format or preflight errors in your task data\. Say yes to let it apply deterministic fixes, or no to get a report of suggested changes instead\.

- **Nothing irreversible happens without you\.** The agent confirms before creating an offer or dataset, finalizing a delivery, or publishing an offer\.

- **Ask for links\.** Ask the agent to report IDs as https://smoldata\.ai/id/… links you can open or share with Smoldata staff\.

## 4\. Core flows

### A\. List off\-the\-shelf data \(offer \+ sample\)

Use this to propose a dataset Smoldata may buy\.

1. **Offer:** the agent generates an offer document, fills it with what you tell it \(at minimum unit price and total volume; add description, domains, languages, quality, provenance, etc\.\), validates it, and creates a draft offer\.

2. **Sample dataset:** the agent creates a **sample** dataset under that offer with the evaluation type you choose\.

3. **Deliver** representative tasks into it \(flow C\)\.

4. **Publish** the offer when you are happy with it\. Review it on **Marketplace**: your offers are the rows with type **Offer**, and unpublished ones show **Draft**\.

|Create a draft Smoldata offer for “Dataset name” and a sample dataset for it\. Ask me for the commercials and dataset details, show me validation results before creating anything, then deliver the sample tasks in /path/to/sample\-tasks\. Don’t publish yet\.|
|---|

![image5\.png](图片和附件/image5.png)

*Marketplace: your offers, and any datasets that still need a listing\.*

### B\. Deliver licensed data \(under contract\)

1. Tell the agent which offer or dataset the contract is for\. It finds the existing **licensed** dataset and keeps delivering into it every time\.

2. If there is no licensed dataset yet, the agent creates one under the existing offer \(or creates the offer first, with the agreed terms, if none exists\)\.

3. Deliver \(flow C\)\.

|Deliver the tasks in /path/to/my\-tasks to Smoldata as licensed data for our “Dataset name” offer, delivery date YYYY\-MM\-DD\. Confirm the dataset with me first\. Start with one pilot task\.|
|---|

### C\. Deliver data \(every delivery\)

Give the agent a directory containing one unpacked subdirectory per task\. It then:

1. **Dry\-runs** the whole directory locally and reports format issues before anything is uploaded\.

2. **Uploads one pilot task** into a new draft delivery\. It is much faster to catch problems on one task than on thousands\.

3. **Waits for validation\.** Vendor preflight \(structure, task\.toml, etc\.\) must pass\. For binary tasks, Oracle also runs \(usually a few minutes; large tasks can take 20 minutes or more\); Oracle failures are warnings and **do not block** finalizing\. The agent fixes or reports issues\.

4. **Uploads the remaining tasks** into the same draft \(same dataset and delivery date\) and waits for validation\.

5. **Finalizes** once every intended task is present and passes preflight\. You will be asked for uploader notes \(partial delivery, delays, known gaps, or “None”\)\. The agent returns a Delivery link to share with Smoldata staff\.

|**Finalizing is permanent\.** It moves the delivery from Draft to Ready and starts full validation\. It does not mean Smoldata has accepted the data\. Keep the delivery in draft while you are still adding or fixing tasks\.|
|---|

### D\. Re\-deliver \(fix already\-finalized data\)

1. Tell the agent which finalized delivery needs fixing\. It creates a **new delivery**, under a new delivery date, in the same dataset, marked as a replacement of the original\. Each delivery can be replaced once; to fix a re\-delivery, replace the latest one\.

2. Include only the replacement tasks, and keep each task’s folder name the same as the task it replaces\. A same\-named task becomes a new version of the original; a renamed one becomes a separate new task\. Put unrelated new tasks in a separate delivery\.

3. The re\-delivery follows flow C \(pilot, validate, rest, finalize\)\.

|Re\-deliver the fixed tasks in /path/to/fixed\-tasks as a replacement for our finalized YYYY\-MM\-DD delivery in “Dataset name”, using delivery date YYYY\-MM\-DD\. Keep the original task names and follow the pilot\-first process\.|
|---|

### E\. Undo an accidental upload

Ask the agent to **archive** the task with a reason \(for example, “uploaded to the wrong dataset”\)\. It leaves active lists but keeps its history, and can be restored\.

### F\. Upload non\-Harbor data \(rare\)

The CLI only accepts Harbor\-format tasks by default\. For data that is **not** Terminal\-Bench tasks or RL environments \(for example raw code repositories or documents\), ask your Smoldata contact to create a special **sample** dataset that accepts non\-Harbor files \(sample data only\)\. Your agent then delivers each folder as\-is, with no format checks, pilot, or preflight\. Smoldata still checks for duplicates after you finalize\.

|**Non\-Harbor data is usually not usable\.** Smoldata can only run Harbor tasks, so we cannot evaluate or train on anything else\. Use this only when your Smoldata contact has asked for it\. Terminal\-Bench tasks and RL environments must always be delivered in Harbor format\.|
|---|

### G\. Track what Smoldata received

Open **Deliveries** to see every delivery batch with its dataset, listing, task count, and acceptance status\. Open a batch to see each task and its review result, and fix any that weren’t accepted\. You can also ask your agent for a status summary\.

![image1\.png](图片和附件/image1.png)

*Deliveries: one row per delivery batch\.*

![image4\.png](图片和附件/image4.png)

*A delivery batch and its tasks\.*

## 5\. Getting help

- **CLI problem:** ask your agent to **file a bug**; it attaches the exact command, error, and IDs\.

- **Blocked:** share the bug link in your vendor chat and tag Chris Li \(chli\) for faster resolution\.

- Never share a setup prompt, stored credential, or upload link\.

## FAQ

**Sample or licensed?** Sample = representative data for evaluation before a purchase\. Licensed = data delivered under an agreed contract\. Ask your Smoldata contact if unsure\.

**Which evaluation type?** binary for pass/fail tasks \(including Terminal\-Bench\), bounded\_continuous for scores in a fixed range, unbounded\_continuous for open\-ended values\. If one offer needs two types, use two datasets\.

**Can I edit a delivery after finalizing?** No\. Use flow D to re\-deliver\.

**My upload was interrupted\.** Ask the agent to rerun the same command with the same dataset, delivery date, task selection, and unchanged files; retries are safe\.

**My setup prompt expired or was used\.** Generate a new one on **Connect an Agent**; it replaces the old one\.

Note: this section describes how to delivery data over S3\. S3 delivery can be used as a backup for licensed data delivery, but smoldata is now the recommended way to send data\. 

## Background

In our system a Task stores generic metadata and can have many associated TaskVersions, which are the specific iterations of the task\. Each Task has a latest TaskVersion, which we will expect to use as the canonical version of the task\. 

We’d like to avoid needing to parse substrings for any of the fields in the S3 keypath\. 

We are comfortable using s3 keypath segments like **“licensed” or “sample”** to derive meaning of the dataset \- eg **“sample”** keypath includes all your sample datasets shared with us before purchase, **“licensed”** include the full dataset delivery after purchase, but we will not attempt to parse prefixes and suffixes in task names or dataset names\. For example a task name with a \_sample will not carry any semantic meaning for us\. 

## Goals

Our format allows Meta to:

1. Track versions of the same task across multiple deliveries

2. Avoid complicated substring parsing to derive meaning about a task

3. Differentiate between licensed and sample tasks

4. Ensure a consistent format for all vendors

5. Ensure compatibility with downstream systems consuming tasks

# Data Delivery Message

For each data delivery, always have a very clear  message in a new thread purely sharing the current data delivery s3 path, and data volume in the vendor Gchat Space with Meta and ping Anlu Xing\(anluxing@meta\.com\) so that we can keep track of\.

# Meta Owned S3 bucket setup and sample data sharing

Once the Gchat space created, share the AWS IAM ARN in the Gchat space and ping Vish Vogeti and Mush Kapadia who can help create the S3 bucket\. Once the bucket is created, you need to upload sample datasets under the key path s3://\{VENDOR\_NAME\}\-licensed/**sample**/, see **Example 4 sample tasks** in the next section\.

# Desired data delivery format

|**Desired **|s3://\{VENDOR\_NAME\}\-licensed/**licensed**/**dataset=**\{DATASET\_NAME\}**/delivery=\{DELIVERY\_ISO8601\_DATE\_UTC\}/**\{TASK\_NAME\}<br>s3://\{VENDOR\_NAME\}\-licensed/**sample**/**dataset=**\{DATASET\_NAME\}**/delivery=\{DELIVERY\_ISO8601\_DATE\_UTC\}/**\{TASK\_NAME\}|
|---|---|

### Combined length invariant

The combined char length the following strings must be less than or equal to 255:

len\(\{VENDOR\_NAME\}\_\{DATASET\_NAME\}\_\{TASK\_NAME\}\) \<= 255

### Allowed characters

We expect all variables to be characters from the set \[a\-z0\-9\_\-\], i\.e\. alpha\-numeric and using snake\_case or kebab\-case

## Example 1 \- Redelivery

This example shows how we expect tasks to be updated \(i\.e\. Redelivered\) if there are changes to a task as a result of feedback given or other improvements which should replace the existing task\.

|**Actions**|**Task delivered to**|**Result**|
|---|---|---|
|**1**|s3://hooli\-licensed/licensed/dataset=tbench\-100/delivery=2026\-06\-08/traveling\_salesman|**✅ Initial delivery of task**|
|**2**|s3://hooli\-licensed/licensed/dataset=tbench\-100/delivery=2026\-06\-**09**/traveling\_salesman|**✅Redelivery of task**, resulting in new task version|
|**3**|s3://hooli\-licensed/licensed/dataset=tbench\-100/delivery=2026\-06\-**10**/traveling\_salesman**\-v2**|**🚫Incorrect Redelivery** resulting in undesired new task creation|

## Example 2 \- Separate deliveries

Adding tasks across multiple days that are not redeliveries\. 

|**Actions**|**Task delivered to**|**Result**|
|---|---|---|
|**1**|s3://hooli\-licensed/licensed/dataset=tbench\-multistep/delivery=2026\-06\-08/traveling\_salesman|**✅ Initial delivery**|
|**2**|s3://hooli\-licensed/licensed/dataset=tbench\-multistep/delivery=2026\-06\-**09**/**n\_sided\_dice**|**✅Initial delivery of new task**|

## Example 3 \- New delivery with some unchanged tasks

Subsequent delivery includes tasks already included in a previous version without changes\.

|**Actions**|**Task delivered to**|**Result**|
|---|---|---|
|**1**|s3://hooli\-licensed/licensed/dataset=tbench\-multistep/delivery=2026\-06\-08/traveling\_salesman|**✅ Initial delivery**|
|**2**|s3://hooli\-licensed/licensed/dataset=tbench\-multistep/delivery=2026\-06\-09/traveling\_salesman<br>s3://hooli\-licensed/licensed/dataset=tbench\-multistep/delivery=2026\-06\-**09**/**n\_sided\_dice**|**✅No\-op for traveling\_salesman**<br>**✅Initial delivery of n\_sided\_dice**|

## Example 4 \- Sample tasks

All sample tasks should be delivered with the keypath s3://\{VENDOR\_NAME\}\-licensed/**sample**/

|**Actions**|**Task delivered to**|**Result**|
|---|---|---|
|**1**|s3://hooli\-licensed/**sample**/dataset=tbench\-multistep/delivery=2026\-06\-08/traveling\_salesman|**✅ Initial delivery**|

# 

