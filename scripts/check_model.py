"""Verify your API key works.
   Free option:  export GEMINI_API_KEY=...     (key from aistudio.google.com)
   Claude:       export ANTHROPIC_API_KEY=...
   then:         python scripts/check_model.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config  # noqa: E402

if config.PROVIDER == "none" or config.MOCK_LLM:
    sys.exit("No API key found (GEMINI_API_KEY or ANTHROPIC_API_KEY). The app still runs in offline MOCK mode without one.")

ok = True
if config.PROVIDER == "gemini":
    import httpx
    from app.llm import GeminiLLM
    print("Provider: Gemini")
    try:
        r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": config.GEMINI_API_KEY}, timeout=30)
        names = {m["name"].split("/")[-1] for m in r.json().get("models", [])} if r.status_code == 200 else set()
        print(f"{'OK  ' if r.status_code == 200 else 'FAIL'} key check: HTTP {r.status_code}, {len(names)} models visible")
        ok = r.status_code == 200
        for m in (config.GEMINI_MODEL, config.GEMINI_JUDGE_MODEL, config.GEMINI_FALLBACK):
            print(f"{'OK  ' if m in names else 'MISSING'} {m}" + ("" if m in names else "  <- set GEMINI_MODEL / GEMINI_FALLBACK to one from your list"))
            ok = ok and m in names
        if ok:
            out = GeminiLLM().judge_names("Acme Fab Pvt Ltd", "Acme Fabrication Private Limited", {})
            print("OK   structured call:", out)
    except Exception as e:
        print("FAIL", type(e).__name__, str(e)[:160])
        ok = False
else:
    import anthropic
    print("Provider: Claude")
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=30)
    tool = {"name": "pong", "description": "Reply", "input_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}
    for job, model in [("extract", config.MODEL_EXTRACT), ("judge", config.MODEL_JUDGE), ("write", config.MODEL_WRITE), ("fallback", config.MODEL_FALLBACK)]:
        for choice in ({"type": "tool", "name": "pong"}, {"type": "auto"}):
            try:
                r = client.messages.create(model=model, max_tokens=200, tools=[tool], tool_choice=choice,
                                           messages=[{"role": "user", "content": "Call the pong tool with ok=true."}])
                used = any(b.type == "tool_use" for b in r.content)
                print(f"OK    {job:<9} {model:<22} tool_choice={choice['type']:<5} tool_called={used}")
                break
            except anthropic.BadRequestError as e:
                print(f"      {job:<9} {model:<22} tool_choice={choice['type']:<5} rejected ({str(e)[:70]}), trying next")
            except Exception as e:
                print(f"FAIL  {job:<9} {model:<22} {type(e).__name__}: {str(e)[:100]}")
                ok = False
                break
print("\nAll good." if ok else "\nSomething needs fixing (see above).")
sys.exit(0 if ok else 1)
