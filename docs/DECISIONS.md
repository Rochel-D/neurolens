# Design decisions

1. **The LLM never invents medical facts.** Tools produce the facts (parsed values, ranges, retrieved notes,
   PubMed titles); the model only rewrites them. This answers the main finding of our LLM Engineering report: the
   fine-tuned 0.5B model explained clearly but still made factual mistakes.

2. **A verifier sits between the model and the user.** Every number near a test name must be that test's value,
   range or a threshold from our knowledge base; "high/low/normal" must match the parsed status; the MRI class and
   confidence wording must match the prediction. A failed draft is rewritten once with the checker's feedback using
   the stronger router model; if it still fails, the user gets the deterministic template built from verified facts.

3. **Guardrails run before tools.** An emergency message never waits for an LLM call. Emergency rules need a
   personal, current context ("I have chest pain") so general questions ("what causes chest pain?") still get answered.

4. **No custom MCP server.** We use the official `mcp-server-fetch` server over stdio for PubMed E-utilities and the
   MedlinePlus web service. If MCP cannot start, the same URLs are fetched directly, so the app never depends on it.

5. **Six keys, ordered.** Free tiers rate-limit often. With each key failing 35% of the time at random, one key
   answers 65% of requests and the six-key pool 99.8% (see `evals/results.md`).

6. **Files are processed in memory.** Reports and scans are never written to disk; only parsed values or
   predictions are stored, linked to the owner, and returned as 404 to anyone else.

7. **Long-term memory is opt-in.** Without consent only the current chat (last 10 messages) is used. Turning
   consent off deletes the profile and memory items. "Delete my data" removes everything.

8. **The report's own reference range wins.** Labs differ, so a range printed on the report is used before our
   standard adult range; the UI shows which one was used.

9. **Honest degradation.** No keys → "template (offline)". No MRI weights → "classifier not installed". These are
   shown to the user rather than hidden.

## Known limitations
* Sample reports and eval sets are small and written by the team; they show the method, not clinical validity.
* The MRI classifier sees one 2D slice from a public dataset; real diagnosis needs the full study and a radiologist.
* The rate limiter is in memory (single server). SQLite suits a demo; production would use PostgreSQL.
* Scanned (image-only) PDFs need OCR (`pytesseract`), which is optional.
* The JWT is kept in `sessionStorage`; an httpOnly cookie + CSRF protection would be stronger.
