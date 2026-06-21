# BABU Multi-Provider Model Architecture & Telemetry Report

This report provides a comprehensive architectural analysis of model configuration across all cognitive units of BABU (Personal Assistant, Planner, Swarm Departments, Immune System, and Context Compressor), diagnoses the root cause of the Gemini 2.5 Flash configuration failure, and outlines a technical blueprint to enable seamless multi-provider switching (Groq, Google Gemini, OpenAI, and OpenRouter).

---

## 1. Environment Credentials Audit

We inspected your local environment vbabubles in `c:\Users\LENOVO\.gemini\antigravity\scratch\Babu\.env`. Here is the current configuration status of your AI API keys:

* **`GROQ_API_KEY`**: **`CONFIGURED`** (Active & working successfully).
* **`TELEGRAM_BOT_TOKEN`**: **`CONFIGURED`** (Active & working successfully).
* **`GEMINI_API_KEY`**: **`NOT CONFIGURED`** (Missing from `.env` file).
* **`OPENROUTER_API_KEY`**: **`NOT CONFIGURED`** (Missing from `.env` file).
* **`OPENAI_API_KEY`**: **`NOT CONFIGURED`** (Missing from `.env` file).

---

## 2. Model Usage Map by Cognitive Unit

Every major unit inside BABU’s cognitive stack has a distinct model configuration. The table below documents which unit is using which model, its backend instantiation class, and its configuration status:

| Cognitive Unit | Purpose | Configured Model | Instantiation Backend | Configuration Status / Gaps |
| :--- | :--- | :--- | :--- | :--- |
| **Personal Assistant (PA)** | Dynamic user-facing response synthesis | `CURRENT_PA_MODEL` (Default: `llama-3.1-8b-instant`) | Built via `build_llm()` | **Operational** (Fully adjustable via `/model <1-8>`) |
| **Swarm Departments** | Web research, deep analysis, citation-lock reports | `CURRENT_DEPT_MODEL` (Default: `llama-3.3-70b-versatile`) | Built via `build_llm()` and dispatched to worker nodes | **Operational** (Fully adjustable via `/model swarm <1-8>`) |
| **Strategic Planner** | Intent-routing and task DAG decomposition | `CURRENT_DEPT_MODEL` (Default: `llama-3.3-70b-versatile`) | **Hardcoded** `ChatGroq(model=model_name)` inside `plan_goal()` | **❌ Architectural Gap**: Bypasses `build_llm()` dynamic routing. Fails if Swarm model is set to a non-Groq model. |
| **Context Compressor** | Staged context compression for research briefings | `"llama-3.1-8b-instant"` | **Hardcoded** `ChatGroq` inside `compress_context_payload()` | **Locked to Groq** (Cannot leverage other providers if Groq is rate-limited). |
| **Immune System** | Diagnostic failure rule synthesis and sealing | `"llama-3.3-70b-versatile"` | **Hardcoded** `ChatGroq` inside `log_execution_failure()` | **Locked to Groq** (Will fail rule synthesis if Groq keys are rate-limited). |

---

## 3. Root Cause: The Gemini 2.5 Flash Swarm Failure

When you ran `/model swarm 6` to switch your research swarm to `gemini-2.5-flash`, the system failed with a "no model found" or "not configured" error. The logs reveal a **two-fold second-order failure**:

1. **Planner Hardcoding Bypass**:
   Inside `planner.py`, `plan_goal()` does **not** call `build_llm()`. Instead, it initializes a generic `ChatGroq` client directly:
   ```python
   llm = ChatGroq(model=model_name, temperature=0.1, api_key=os.environ.get("GROQ_API_KEY", ""))
   ```
   When `model_name` is set to `"gemini-2.5-flash"`, the system attempts to request a model named `gemini-2.5-flash` from **Groq's servers**. Since Groq does not host Gemini models, the Groq API returns a `404 Model Not Found` or `400 BadRequest`, causing the planner to fail.

2. **Gemini Key and Code Missing**:
   The current `build_llm()` constructor inside `bot.py` does not have active code to build `ChatGoogleGenerativeAI` from `langchain_google_genai`. Instead, it silently redirects all `"gemini-"` prefixes to `"llama-3.1-8b-instant"` on Groq:
   ```python
   if target_model.startswith("gemini-"):
       target_model = "llama-3.1-8b-instant"
   ```
   If you configure `GEMINI_API_KEY` in `.env`, the code is currently unable to establish a direct connection to Google’s actual Gemini Flash endpoint.

---

## 4. Proposed Solution: Multi-Provider Architectural Blueprint

To solve these model configuration issues and fully equip BABU with Groq, Gemini, OpenAI, and OpenRouter, we can execute the following three upgrades:

### Upgrade A: Unified LLM Construction across all layers
Modify `planner.py`, `memory.py`, and `bot.py` to import and call a unified `build_llm` function instead of hardcoding `ChatGroq` directly. This guarantees that all cognitive units (including the Planner, Compressor, and Immune System) respect your active model choice and routing rules.

### Upgrade B: Integrate OpenRouter and Google Gemini in `build_llm`
Re-engineer `build_llm` to inspect keys and return the correct provider class:
```python
def build_llm(model_name: str, temp: float):
    # 1. Google Gemini integration
    if model_name.startswith("gemini-"):
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if gemini_key:
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(model=model_name, temperature=temp, google_api_key=gemini_key)
        else:
            # Fallback to Groq if key is not configured to maintain degraded service
            print("[LLM REDIRECT] Gemini key missing, falling back to Llama on Groq.", flush=True)
            return ChatGroq(model="llama-3.1-8b-instant", temperature=temp)

    # 2. OpenRouter integration
    elif model_name.startswith("openrouter/"):
        or_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not or_key:
            raise ValueError("OPENROUTER_API_KEY is not configured in environment vbabubles.")
        from langchain_openai import ChatOpenAI
        # Strip openrouter/ prefix for the endpoint call if needed, or pass it directly
        target_model = model_name.replace("openrouter/", "")
        return ChatOpenAI(
            model=target_model,
            temperature=temp,
            api_key=or_key,
            base_url="https://openrouter.ai/api/v1"
        )
        
    # 3. OpenAI integration
    elif model_name.startswith("gpt-"):
        # existing OpenAI logic...
```

### Upgrade C: Upgraded `/model` Governance Telemetry
Enhance the `/model` Telegram command to check the configuration status of your API keys in real time, displaying `[Active]` or `[Missing Key]` next to each model. This gives you complete, transparent observability over your active backends:

```text
BABU Model Settings

- Current PA: llama-3.1-8b-instant
- Current Swarm: llama-3.3-70b-versatile

*Available Providers:*
🟢 Groq: ACTIVE (5 models available)
🔴 Google Gemini: MISSING KEY (Routes to Groq Llama)
🔴 OpenRouter: MISSING KEY (Unavailable)
🔴 OpenAI: MISSING KEY (Unavailable)
```
