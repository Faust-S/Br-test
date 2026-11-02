#!/usr/bin/env python3
"""Measure screenshot fidelity against an independent, documented reference.

No synthetic pass: without a reference and explicit acceptance thresholds,
report status MEASURED_NOT_QUALIFIED. Same viewport/DPR/state is mandatory.
"""
import argparse
import json
from pathlib import Path
from PIL import Image, ImageChops, ImageFilter, ImageStat

def score(reference: Image.Image, candidate: Image.Image) -> dict:
    if reference.size != candidate.size:
        raise ValueError(f"Viewport mismatch: expected {reference.size}, got {candidate.size}")
    if not reference.width or not reference.height:
        raise ValueError("Empty image")
    a, b = reference.convert("RGB"), candidate.convert("RGB")
    diff = ImageChops.difference(a, b)
    stat = ImageStat.Stat(diff)
    per_pixel = list(diff.getdata())
    count = len(per_pixel)
    errors = sorted(max(ch) for ch in per_pixel)
    changed = sum(v > 16 for v in errors)
    # Edges highlight geometry/text shifts independently from flat colors.
    edges_a = a.convert("L").filter(ImageFilter.FIND_EDGES)
    edges_b = b.convert("L").filter(ImageFilter.FIND_EDGES)
    edge_diff = ImageChops.difference(edges_a, edges_b)
    return {
        "size_px": [a.width, a.height],
        "rgb_mae_0_to_255": round(sum(stat.mean) / 3, 4),
        "max_channel_error_p95_0_to_255": errors[min(count - 1, int(.95 * count))],
        "changed_pixels_over_16_fraction": round(changed / count, 6),
        "edge_mae_0_to_255": round(ImageStat.Stat(edge_diff).mean[0], 4),
        "exact_match_fraction": round(sum(e == 0 for e in errors) / count, 6),
    }

def evaluate(reference: Path, candidate: Path, provenance: Path,
             max_mae: float | None, max_changed: float | None) -> dict:
    info = json.loads(provenance.read_text(encoding="utf-8"))
    required = ["reference_engine", "reference_version", "viewport_css_px",
                "device_pixel_ratio", "page_url", "capture_state", "capture_date"]
    missing = [k for k in required if k not in info]
    if missing:
        raise ValueError("Missing reference provenance: " + ", ".join(missing))
    if not info["reference_engine"] or not info["capture_state"]:
        raise ValueError("Reference engine and capture state must be non-empty")
    with Image.open(reference) as ref, Image.open(candidate) as actual:
        metrics = score(ref, actual)
    expected = info["viewport_css_px"]
    dpr = float(info["device_pixel_ratio"])
    if [round(expected[0] * dpr), round(expected[1] * dpr)] != metrics["size_px"]:
        raise ValueError("Reference provenance viewport/DPR disagrees with screenshots")
    result = {"reference": str(reference), "candidate": str(candidate),
              "reference_provenance": info, "metrics": metrics,
              "status": "MEASURED_NOT_QUALIFIED"}
    if max_mae is not None and max_changed is not None:
        result["thresholds"] = {"max_mae": max_mae, "max_changed_fraction": max_changed}
        result["status"] = ("PASS" if
            metrics["rgb_mae_0_to_255"] <= max_mae and
            metrics["changed_pixels_over_16_fraction"] <= max_changed else "FAIL")
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--max-mae", type=float)
    parser.add_argument("--max-changed-fraction", type=float)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if (args.max_mae is None) != (args.max_changed_fraction is None):
        parser.error("Use both acceptance thresholds or neither")
    if args.max_mae is not None and (args.max_mae < 0 or not 0 <= args.max_changed_fraction <= 1):
        parser.error("Invalid acceptance thresholds")
    result = evaluate(args.reference, args.candidate, args.provenance,
                      args.max_mae, args.max_changed_fraction)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    if result["status"] == "FAIL":
        raise SystemExit(1)

if __name__ == "__main__":
    main()
