# HappyRobot platform integration

This document is for **workflow builders** wiring an inbound **web call** agent to the Freight Loads API. The API is a **decision engine** (FMCSA check, load search, negotiation math, persistence, metrics). **Live voice**—questions, pitch, confirmations, transfer UX—is built in HappyRobot using their inbound call trigger, prompts, and **HTTP tools** that call this backend.

**Base URL:** `https://<your-deployed-host>` (use TLS in production).  
**Auth:** every request sends header `X-API-Key: <same value as server API_KEY>`.

---

## Expected workflow structure (high level)

1. **Trigger:** Inbound **Web Call** (or equivalent) starts the workflow when a carrier connects.
2. **MC capture:** Agent asks for MC / DOT docket → **HTTP tool** `GET /v1/verify-carrier/mc/{mc_number}` with the digits from the conversation in the path.
3. **Invalid MC:** If `valid` is `false`, agent politely explains `reason`, ends the call (no need to call `process-call` unless you want a logged row—today the API does not require it).
4. **Valid MC:** Agent asks what lane or load they care about → **HTTP tool** `GET /v1/search-loads?origin=...&destination=...&equipment_type=...` (add rate/miles/pickup filters if collected).
5. **Pitch:** Agent reads `pitch_text` for one or more rows (and can mention `load_id` explicitly).
6. **Interest:** Agent asks “Are you interested?” If **no**, call `POST /v1/process-call` with `carrier_interested: false` and the transcript so far, then branch on `next_action: "end_call"`.
7. **Negotiation (up to 3 rounds):** After **yes**, for each numeric counter from the carrier:
   - Append the new amount to `counter_offers` (cumulative list, newest last).
   - Set `current_round` to `len(counter_offers)` when you send it (optional but recommended).
   - Call `POST /v1/process-call` with `transcript`, `mc_number`, `interested_load_id`, `counter_offers`, `current_round`, and `carrier_interested: true` or omit when inferred.
   - Read **`outcome`**, **`next_action`**, **`suggested_counter`**, **`transfer_message`**, **`transfer_status_message`** to decide the next node.
8. **Booked:** When `outcome` is `booked` and `next_action` is `transfer_to_sales`, run your **transfer** node (or mock): speak `transfer_message` / `transfer_status_message`, then wrap up. This repo does not place the phone call—it only sets flags and copy.
9. **Observability:** Each successful `process-call` persists a **Call** row; `GET /v1/metrics` and `GET /v1/calls` power your dashboard.

**Note:** `POST /v1/process-call` usually returns **HTTP 200** with a business outcome in the JSON body. Do not assume non-200 for `no-interest` or `rejected-price`. Use **`422`** only for invalid JSON / validation (e.g. `current_round` not equal to `len(counter_offers)`).

---

## Endpoint → HappyRobot tool mapping

| Endpoint | When to call |
|----------|----------------|
| `GET /v1/verify-carrier/mc/{mc_number}` | Right after MC is captured verbally. |
| `GET /v1/search-loads` | After lane / equipment (and optional filters) are known, before deep negotiation. |
| `POST /v1/process-call` | End of each negotiation turn, after interest confirmation, or when carrier declines. |

---

## `POST /v1/process-call` — example request bodies

### Round 1 — first counter

Carrier agreed to discuss load `LD-1001` and just said they need **$2,400**.

```json
{
  "transcript": "Agent: What rate works? Carrier: We need 2400 on LD-1001.",
  "mc_number": "MC-123456",
  "interested_load_id": "LD-1001",
  "counter_offers": [2400.0],
  "final_agreed_price": null,
  "carrier_interested": true,
  "current_round": 1
}
```

### Round 2 — second counter (cumulative list)

```json
{
  "transcript": "…prior text… Agent: Posted is 2200. Carrier: We can do 2300.",
  "mc_number": "MC-123456",
  "interested_load_id": "LD-1001",
  "counter_offers": [2400.0, 2300.0],
  "final_agreed_price": null,
  "carrier_interested": true,
  "current_round": 2
}
```

### Round 3 — third (final) structured round

```json
{
  "transcript": "… Carrier: 2100 is our last number.",
  "mc_number": "MC-123456",
  "interested_load_id": "LD-1001",
  "counter_offers": [2400.0, 2300.0, 2100.0],
  "final_agreed_price": null,
  "carrier_interested": true,
  "current_round": 3
}
```

### Not interested (explicit)

Overrides any counters—human said no.

```json
{
  "transcript": "Carrier: Not interested in that one, thanks.",
  "mc_number": "MC-123456",
  "interested_load_id": "LD-1001",
  "counter_offers": [2400.0],
  "carrier_interested": false,
  "interested_reason": "Lane ok, rate too low",
  "current_round": 1
}
```

### Successful booking (example response shape)

After an acceptable last offer (within policy vs. posted rate), the API returns something like:

```json
{
  "outcome": "booked",
  "agreed_price": 2100.0,
  "next_action": "transfer_to_sales",
  "sentiment": "positive",
  "load_id": "LD-1001",
  "loadboard_rate": 2000.0,
  "carrier_mc": "MC-123456",
  "counter_offers": [2400.0, 2300.0, 2100.0],
  "rounds_used": 3,
  "carrier_interested": true,
  "transfer_message": "Great, I'll transfer you to a sales rep now…",
  "transfer_initiated": true,
  "transfer_status_message": "Transfer was successful and now you can wrap up the conversation with the carrier.",
  "followup_needed": false,
  "sentiment_warning": false
}
```

**How the agent should use it**

| Field | Use |
|-------|-----|
| `next_action` | `transfer_to_sales` → go to transfer / wrap-up path; `continue_negotiation` → ask carrier to respond to `suggested_counter`; `end_call` → polite close. |
| `suggested_counter` | Speak as the broker’s suggested figure when `outcome` is `negotiated`. |
| `suggested_counter_reason` | Optional short explanation for the LLM or sub-agent. |
| `transfer_message` | Softer TTS line before/after transfer. |
| `transfer_status_message` | Spec-style status line for the rep handoff (mock). |
| `followup_needed` | If `true`, CRM / dashboard highlight (e.g. soft no with positive sentiment). |
| `sentiment_warning` | If `true` on `booked`, flag tension vs. outcome for QA. |

---

## Error responses (tool branching)

- **Auth (middleware):** `401` / `503` may return JSON `{ "detail": "<string>", "error_code": "INVALID_API_KEY" | "API_KEY_NOT_CONFIGURED" }`.
- **`GET /v1/verify-carrier/mc/...` errors:** `detail` is an object: `{ "message": "<short>", "error_code": "INVALID_MC_INPUT" | "FMCSA_NOT_CONFIGURED" | "FMCSA_UPSTREAM_ERROR" }`.
- **Validation:** `422` with FastAPI’s standard `detail` list for body/query issues.

---

## CORS and HTTPS

- **HTTPS:** Terminate TLS at your reverse proxy (e.g. nginx in this repo’s Compose) so HappyRobot tools hit `https://…`.
- **CORS:** The API sets `Access-Control-Allow-Origin: *` so browser-based testers and permissive tool stacks work. Server-side HappyRobot HTTP tools typically do not rely on CORS.

---

## Voice vs. API boundary

| In HappyRobot | In this API |
|----------------|-------------|
| Greeting, MC question, lane questions, reading `pitch_text`, “interested?”, counters in natural language | `verify-carrier`, `search-loads`, `process-call` |
| Transfer UX, hold music, real PSTN bridge | Not implemented—use `transfer_*` fields as mock copy only |

Deliverable checklist for the challenge: **link to your published HappyRobot workflow** + this **backend** deployed with a public URL + **dashboard** hitting `/v1/metrics` and `/v1/calls`.
