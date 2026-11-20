# Architecture

```mermaid
flowchart TB
  UI[Web app<br/>Simple view / Doctor view] --> API[FastAPI<br/>auth · uploads · chat · memory · admin]
  API --> LP[lab_parser<br/>PDF/text → values, ranges, status]
  API --> MRI[mri<br/>EfficientNet-B0 + Grad-CAM]
  API --> AG
  subgraph AG[LangGraph agent]
    G1[guard_input] --> M[load_memory] --> P[plan] --> A[act] --> E[explain] --> V[verify]
    V -- fail, retry once --> E
    V --> G2[guard_output]
  end
  A --> S1[lab_agent] & S2[neuro_agent] & S3[term_agent] & S4[research_agent] & S5[memory_agent]
  S1 & S2 & S3 --> RAG[(RAG knowledge base<br/>67 docs)]
  S2 & S4 & S3 --> MCP[MCP client] --> FS[[mcp-server-fetch<br/>official, stdio]] --> NLM[PubMed · MedlinePlus]
  S3 --> SER[Serper]
  P & E --> R[Model router<br/>Gemini ×3 → Grok/Groq ×3]
  E --> Q[Fine-tuned Qwen 0.5B<br/>SFT + DPO adapters]
  M --> DB[(SQLite<br/>chat · profile · memory · reports · traces)]
```

## Module boundaries (deep modules)

| Module | Public call | What it hides |
|---|---|---|
| `model_router` | `complete(system, messages, json_mode)` | 6 keys, two API formats, cooldowns, 404 model fallback, JSON validation |
| `lab_parser` | `parse_report(text, sex)` | 43 tests × aliases, unit conversion (mmol/L, µmol/L, lakh/cumm, cells/cumm), printed vs standard ranges, sex ranges, critical values |
| `mri` | `analyse(bytes)` | upload checks, greyscale guard, preprocessing, inference, Grad-CAM overlay |
| `rag` | `retrieve(query, k)` | corpus building, hybrid word + character TF-IDF (or dense embeddings if enabled) |
| `explainer` | `explain(question, facts, audience)` | local fine-tuned model vs router vs template, prompts |
| `verifier` | `verify_lab / verify_mri / verify_citations` | number grounding, status-word matching, confidence wording |
| `guardrails` | `check_input / check_output` | rule sets for emergencies, self-harm, injection, dosing, diagnosis |
| `memory` | `short_term / long_term / remember / forget_everything` | consent rules, limits, cascade deletion |
| `agent.graph` | `run_agent(...)` | the whole state machine, tracing, persistence |

## Neuro vs general scope

* **Neuro only:** MRI classifier (glioma, meningioma, pituitary, no tumour), Grad-CAM, neuro knowledge notes, PubMed research on the predicted pattern.
* **General (all specialties):** lab reports (CBC, iron, diabetes, lipids, kidney, electrolytes, thyroid, liver, vitamins, inflammation) and any medical term (knowledge base first, then MedlinePlus via MCP, then Serper).
