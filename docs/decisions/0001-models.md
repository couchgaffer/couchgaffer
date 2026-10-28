# 0001: Models for the manager and host agents

- **Status:** decided, 23 Oct 2026 (#6)
- **Decision:** `gpt-5.4-mini`, reasoning effort `none`, for both the manager agents and the host agent. Deployed in Sweden Central as Data Zone Standard (EU), 200K tokens per minute. `gpt-4.1-mini` stays deployed as the tested runner-up.

## Context

- **The gate (agreed 21 Oct):** a plain stat card goes out from code at once. The personal explanation must arrive within **4 s at p95** over 30 scripted moments, measured in the region where the app will run. The host's suggested budget is 2.5 s at p95.
- **Accuracy matters as much as speed.** Every number shown must come from the points engine (the evidence rule, `contracts/README.md`). A fast model that invents numbers would break the product's main claim.
- **Constraints:**
  - The subscription is new pay-as-you-go. It has **no quota** for the larger models (`gpt-5.4`, `gpt-5.5`, `gpt-5.6-*`, `gpt-6-*`); those need a quota request.
  - The model must stay supported for as long as the demo runs.
  - Rules: Microsoft Foundry is a hero technology (rule 6.2), and all data is synthetic (rule 4.7).
- **Region:** Sweden Central, suggested in the Reactor session for model availability. East US 2 offered the same models; Sweden Central keeps the data in the EU.

## Options

All have quota today; retirement dates are from the Azure model catalogue, 23 Oct.

| Option | Tier | Retires | Why it was in the test |
|---|---|---|---|
| `gpt-4.1-mini` | Global Standard | 14 Apr 2027 | Non-reasoning, usually fast |
| `gpt-5-mini`, reasoning `minimal` | Global Standard | 9 Feb 2027 | Largest quota (500K tokens per minute) |
| `gpt-5.4-mini`, reasoning `none` | Data Zone Standard (EU) | 21 Sep 2027 | Newest small model with quota |
| `gpt-5.4-mini`, reasoning `low` | Data Zone Standard (EU) | 21 Sep 2027 | Does a little reasoning buy accuracy? |

Excluded: `o4-mini` (retires 19 Nov 2026, too soon). Larger models: no quota.

## Method

- **Tool:** `backend/tools/latency_test.py`, tested in `backend/tests/test_latency_tool.py`.
- **Moments:** 30 scripted moments (`backend/tools/latency_fixtures.py`) with 1 to 3 managers at stake each, 52 manager calls in all. On 23 Oct one manager wrote in Hindi, the heavier case for tokens. Since #16 the fixture uses the league's Spanish manager instead, as the 24 Oct rerun (below) did.
- **Prompts:** each call uses the planned prompt shape and strict JSON output (the `Bid` fields).
- **Measured per candidate:**
  - every manager call, run concurrently per moment, as the app will
  - the slowest manager per moment: the explanation is ready only when the last concurrent call returns, so **this is the gated number**
  - the host call
  - the batch fallback: one call for all managers at stake
- **Quality:** valid output (all fields, lengths, cited evidence IDs exist), and the number check: every number in the text must match a cited evidence value.
- **Conditions:** the same conditions the app will use: keyless Entra ID, `store=False`, no SDK retries, a 30 s ceiling. A failed or timed-out call counts as a failure at its elapsed time. Each model gets one warm-up call first.
- **Runs:**
  - **In region (the gate):** an Azure Container Instance in Sweden Central with a managed identity, 23 Oct.
  - **Two runs from a laptop in India:** an upper bound only (see "Laptop runs" below).

## Results: in region, 23 Oct

| Candidate | Slowest manager p95 | Manager p50 / p95 | Valid | Numbers OK | Host p95 (numbers OK) | Batch p95 (valid) |
|---|---|---|---|---|---|---|
| **gpt-5.4-mini, none** | **2.5 s** | 1.5 / 2.1 s | 96%* | 96%* | **1.9 s** (100%) | 3.0 s (100%) |
| gpt-4.1-mini | 3.7 s | 2.1 / 3.6 s | 100% | 100% | 1.9 s (93%) | 5.4 s (93%) |
| gpt-5-mini, minimal | 4.3 s | 2.2 / 3.7 s | 98% | **71%** | 2.8 s (97%) | 4.7 s (93%) |
| gpt-5.4-mini, low | 4.4 s | 2.6 / 4.3 s | 98% | 98% | 2.6 s (100%) | 5.7 s (100%) |

One `gpt-5.4-mini` call timed out (one of 52 for its setting); it counts as a failure above.

\* Re-measured 24 Oct under the production rules (#28): a bid must cite evidence, and its numbers must match the cited evidence with their sign and unit. The 23 Oct run accepted uncited bids and scored 98% valid. The rerun was from a laptop, so only its accuracy replaces the in-region figures; its slowest manager p95 was 2.9 s, still inside the 4 s gate. The other rows were measured under the looser rule.

## Decision and why

**`gpt-5.4-mini` with reasoning `none`, for managers and host.**

1. **It passes the gate with the most headroom:** 2.5 s against 4 s for the slowest manager per moment. Its host p95 of 1.9 s is inside the 2.5 s budget.
2. **Accuracy is good enough to sit behind the number checker:** 96% of manager outputs and 100% of host outputs passed the number check first time. The checker in #10 retries once and then falls back to a plain stat, so the rare miss never reaches a screen.
3. **The fallback design also passes:** the batched variant was 3.0 s at p95 and 100% valid. If load or latency changes, there is a working plan B on the same model.
4. **The longest support window** of the options: it retires 21 Sep 2027.
5. **EU data zone:** prompts and outputs stay in the EU. That is not required for synthetic data, but it is an honest line for the Enterprise category.
6. **One model for both roles** keeps one prompt style, one quota and one thing to monitor.

### Why not the others

- **`gpt-4.1-mini`:** it passes (3.7 s) with perfect manager accuracy, but with less headroom, a weaker host (93% numbers OK) and a batch fallback over the gate (5.4 s). It is already "Legacy" in the catalogue. It is cheaper, so it stays deployed as the tested runner-up if `gpt-5.4-mini` degrades or is withdrawn.
- **`gpt-5-mini`, minimal:** it fails on accuracy: only 67% to 71% of explanations passed the number check, in every run. Its deployment has been deleted.
- **`gpt-5.4-mini`, low:** reasoning bought no accuracy over `none`, but doubled output tokens and pushed p95 over the gate (4.4 s).
- **A larger host model,** as first planned: no quota on this subscription, and the small model already meets the host budget with 100% number accuracy. Revisit only if quota is granted and host reasons look weak in the demo.

## Consequences

- **Settings for the agents (#10, #11):**
  - deployment `gpt-5.4-mini`, `reasoning.effort = "none"`
  - Responses API with strict JSON schema, `store=False`
  - `max_retries=0` on the client, with our own deadline (4 s for managers, suggested 2.5 s for the host); a deadline miss leads to the fallback
  - keyless auth (`DefaultAzureCredential`)
- **Tokens:** one 3-minute match of about 13 moments uses about 22 manager calls and at most 13 host calls: about 19K input and 4K output tokens, before the half-time and full-time summaries.
- **Capacity:** 200K tokens per minute, the whole Data Zone quota, is about 25 simultaneous matches. #9's session cap should sit below that. More needs a quota request.
- **Network:** from a laptop in India the same model measured 2.9 s and 4.7 s across two runs (see below). Judges far from Sweden see the same model latency; the app is deployed next to the models, so only the WebSocket hop is longer.

## Laptop runs (India to Sweden Central), for the record

| Run | Notes | gpt-5.4-mini, none: slowest manager p95 |
|---|---|---|
| 1 | 30 connection errors on `gpt-4.1-mini` (network); batch result void (bug, fixed) | 2.9 s |
| 2 | One timeout inflated to 91 s by SDK retries (bug, fixed: `max_retries=0`) | 4.7 s |

Bugs found in the tool during these runs, each fixed with a test that failed first:
- the two reasoning settings of one deployment were merged into one row
- the batch output field didn't match its input (`manager_id`)
- hidden SDK retries

## Revisit if

- The in-region p95 for the slowest manager rises above 3.5 s, or more than 5% of outputs fail the number check after prompt changes. Rerun the tool.
- Quota arrives for a larger model, and the host's reasons read poorly in the demo.
- `gpt-5.4-mini` is withdrawn or degraded: switch to `gpt-4.1-mini` and rerun.

## How to rerun

Locally, after `az login`, from `backend/`:

```
FOUNDRY_ENDPOINT=https://<foundry>.cognitiveservices.azure.com/ \
  uv run python -m tools.latency_test gpt-5.4-mini:none gpt-4.1-mini --out results.json
```

In region:
1. `az acr build -r <registry> -t latency-test:<tag> -f backend/tools/Dockerfile .` from the repo root.
2. Create a user-assigned identity with `AcrPull` on the registry and `Cognitive Services OpenAI User` on the Foundry resource.
3. Run the image as a container instance in Sweden Central with that identity, `FOUNDRY_ENDPOINT` and `AZURE_CLIENT_ID`.
4. Read the logs, then delete the container, the identity and its roles.
