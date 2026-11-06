"""JSON readback of the exact shared U03 Programs Center fixtures in Python."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from program_card import project_program_card

scenarios = []
for name in ("program-card-scenarios.json", "agent03-owner-projection.json"):
    scenarios.extend(json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8")))
print(json.dumps(
    [{"case": case["case"], "projection": project_program_card(**case["args"])}
     for case in scenarios],
    ensure_ascii=False, sort_keys=True, separators=(",", ":")
))
