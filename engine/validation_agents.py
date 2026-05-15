"""
=============================================================================
Validation Agents for Claude Code
=============================================================================

These agents act as QA checkpoints during Claude Code development.
They detect drift between specifications and implementation.

Use cases:
  - "Claude Code, run validation agents and report any mismatches"
  - pytest hook to fail builds when spec/impl diverge
  - Pre-commit check before pushing changes

NOT for end users — purely a development quality tool.

Run all:
    python -m engine.validation_agents

Run specific:
    python -m engine.validation_agents --agent spec
    python -m engine.validation_agents --agent algorithm
    python -m engine.validation_agents --agent ui
"""

import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Dict, Any, Optional


class CheckStatus(str, Enum):
    PASS = "✅"
    FAIL = "❌"
    WARN = "⚠️"
    SKIP = "⏭️"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    expected: Any = None
    actual: Any = None
    message: str = ""
    fix_hint: str = ""


@dataclass
class ValidationReport:
    agent: str
    checks: List[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.PASS)

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.FAIL)

    @property
    def all_passed(self) -> bool:
        return self.failed == 0


# =============================================================================
# Agent 1: Spec Validator
# Detects: mockup vs implementation drift
# =============================================================================

class SpecValidator:
    """
    Validates that the implementation matches the design specification.

    Specifically catches:
      - Sample data drift (e.g. mockup uses 36 EA but real Excel has 44 EA)
      - Truck dimension mismatch
      - Calibration overrides missing in app code
    """

    name = "Spec Validator"

    # SOURCE OF TRUTH — kept in sync with docs/DESIGN_SPEC.md
    EXPECTED = {
        "sample_l001": {
            "models": ["LF29H8330S", "WM4000HWA", "DLEX4000W", "LDFN4542S", "LWS3063ST"],
            "total_units": 36,
            "description": "Mockup uses fridge+washer+dryer+dish+oven (36 EA)",
        },
        "calibrations": [
            {"model": "LDFN4542S", "stackable": True, "load_bear_kg": 60, "fragile": False},
            {"model": "LWS3063ST", "stackable": True, "load_bear_kg": 90, "fragile": False},
        ],
        "truck_26ft": {
            "length_mm": 7925,
            "width_mm": 2438,
            "height_mm": 2590,
            "door_track_loss_mm": 250,
        },
    }

    def validate(self, project_root: Path) -> ValidationReport:
        report = ValidationReport(agent=self.name)

        # Check 1: sample_input.xlsx has expected L001 composition
        try:
            import pandas as pd
            xl_path = project_root / "data" / "sample_input.xlsx"
            df = pd.read_excel(xl_path, sheet_name="Loads")
            l001 = df[df["load_id"] == "L001"]
            actual_models = sorted(l001["model_code"].tolist())
            expected_models = sorted(self.EXPECTED["sample_l001"]["models"])
            actual_total = int(l001["quantity"].sum())
            expected_total = self.EXPECTED["sample_l001"]["total_units"]

            if set(actual_models) != set(expected_models):
                missing = set(expected_models) - set(actual_models)
                extra = set(actual_models) - set(expected_models)
                report.checks.append(CheckResult(
                    name="sample_l001_models",
                    status=CheckStatus.FAIL,
                    expected=expected_models,
                    actual=actual_models,
                    message=f"L001 model composition differs from mockup design",
                    fix_hint=(
                        f"Missing: {missing}, Extra: {extra}. "
                        f"Either update data/build_sample_excel.py to match design spec, "
                        f"or update docs/DESIGN_SPEC.md if the new composition is intentional."
                    ),
                ))
            elif actual_total != expected_total:
                report.checks.append(CheckResult(
                    name="sample_l001_quantity",
                    status=CheckStatus.FAIL,
                    expected=expected_total,
                    actual=actual_total,
                    message=f"L001 total units {actual_total} ≠ design spec {expected_total}",
                    fix_hint="Update sample Excel or update mockup spec to match",
                ))
            else:
                report.checks.append(CheckResult(
                    name="sample_l001_composition",
                    status=CheckStatus.PASS,
                    message=f"L001 matches design: {expected_total} EA across {len(expected_models)} models",
                ))
        except Exception as e:
            report.checks.append(CheckResult(
                name="sample_l001_composition",
                status=CheckStatus.FAIL,
                message=f"Could not load sample Excel: {e}",
            ))

        # Check 2: Truck master matches spec
        try:
            df_trucks = pd.read_excel(xl_path, sheet_name="Truck_Master")
            t26 = df_trucks[df_trucks["truck_type"] == "26ft"].iloc[0]
            for key, expected_val in self.EXPECTED["truck_26ft"].items():
                if key == "door_track_loss_mm":
                    continue  # not in Excel, in code
                actual_val = int(t26[key])
                if actual_val != expected_val:
                    report.checks.append(CheckResult(
                        name=f"truck_26ft_{key}",
                        status=CheckStatus.FAIL,
                        expected=expected_val,
                        actual=actual_val,
                        message=f"26ft truck {key}: {actual_val} ≠ spec {expected_val}",
                        fix_hint="Update Truck_Master sheet to match design spec",
                    ))
            if all(c.name.startswith("truck_26ft_") and c.status == CheckStatus.PASS
                   for c in report.checks if c.name.startswith("truck_26ft_")):
                report.checks.append(CheckResult(
                    name="truck_26ft_dimensions",
                    status=CheckStatus.PASS,
                    message="26ft truck dimensions match spec",
                ))
        except Exception as e:
            report.checks.append(CheckResult(
                name="truck_26ft_dimensions",
                status=CheckStatus.WARN,
                message=f"Could not verify truck dimensions: {e}",
            ))

        # Check 3: app.py applies calibrations
        try:
            app_py = (project_root / "app.py").read_text()
            for cal in self.EXPECTED["calibrations"]:
                model = cal["model"]
                if model not in app_py:
                    report.checks.append(CheckResult(
                        name=f"calibration_{model}",
                        status=CheckStatus.WARN,
                        message=f"app.py does not reference {model} calibration",
                        fix_hint=(
                            f"app.py should override master['{model}'] "
                            f"with stackable={cal['stackable']}, load_bear_kg={cal['load_bear_kg']}, "
                            f"fragile={cal['fragile']} before simulation. "
                            f"Without this, fitted count will be wrong."
                        ),
                    ))
                else:
                    report.checks.append(CheckResult(
                        name=f"calibration_{model}",
                        status=CheckStatus.PASS,
                        message=f"app.py references {model} calibration",
                    ))
        except Exception as e:
            report.checks.append(CheckResult(
                name="calibrations",
                status=CheckStatus.WARN,
                message=f"Could not check app.py calibrations: {e}",
            ))

        # Check 4: app.py uses best_packer.simulate (not py3dbp)
        try:
            app_py = (project_root / "app.py").read_text()
            uses_best = "best_packer" in app_py or "from engine.best_packer" in app_py
            uses_py3dbp = "from engine.packer import simulate_pack" in app_py
            if uses_best and not uses_py3dbp:
                report.checks.append(CheckResult(
                    name="engine_used",
                    status=CheckStatus.PASS,
                    message="app.py uses best_packer (pair-packing)",
                ))
            elif uses_py3dbp and not uses_best:
                report.checks.append(CheckResult(
                    name="engine_used",
                    status=CheckStatus.FAIL,
                    expected="best_packer.simulate",
                    actual="packer.simulate_pack (py3dbp)",
                    message="app.py still uses legacy py3dbp engine",
                    fix_hint=(
                        "Replace 'from engine.packer import simulate_pack' with "
                        "'from engine.best_packer import simulate'. "
                        "Without this, simulation results differ from mockup."
                    ),
                ))
            else:
                report.checks.append(CheckResult(
                    name="engine_used",
                    status=CheckStatus.WARN,
                    message="app.py uses both engines — clean up legacy imports",
                ))
        except Exception as e:
            report.checks.append(CheckResult(
                name="engine_used",
                status=CheckStatus.WARN,
                message=f"Could not check engine usage: {e}",
            ))

        return report


# =============================================================================
# Agent 2: Algorithm Validator
# Detects: regression in pair-packing performance
# =============================================================================

class AlgorithmValidator:
    """
    Validates that the pair-packing algorithm produces expected results.

    Specifically catches:
      - Regression where compactness gets worse
      - Models not hitting max lane count
      - Stack rules violated
    """

    name = "Algorithm Validator"

    # Known-good baseline (from validated sample run)
    BASELINE = {
        "L001_36EA_design_spec": {
            "orders": [
                {"model_code": "LF29H8330S", "quantity": 6},
                {"model_code": "WM4000HWA",  "quantity": 8},
                {"model_code": "DLEX4000W",  "quantity": 8},
                {"model_code": "LDFN4542S",  "quantity": 10},
                {"model_code": "LWS3063ST",  "quantity": 4},
            ],
            "calibrations": {
                "LDFN4542S": {"stackable": True, "load_bear_kg": 60, "fragile": False},
                "LWS3063ST": {"stackable": True, "load_bear_kg": 90, "fragile": False},
            },
            "expected": {
                "fits": True,
                "fitted_count": 36,
                "x_used_ft_max": 24.5,        # ≤ 24.5 ft
                "compactness_pct_max": 95,
                "lane_count": {                # exact lane counts per model
                    "LF29H8330S": 2,
                    "WM4000HWA": 3,
                    "DLEX4000W": 3,
                    "LDFN4542S": 3,
                    "LWS3063ST": 2,
                },
            },
        },
    }

    def validate(self, project_root: Path) -> ValidationReport:
        report = ValidationReport(agent=self.name)

        try:
            sys.path.insert(0, str(project_root))
            from engine.best_packer import simulate
            import pandas as pd

            xl_path = project_root / "data" / "sample_input.xlsx"
            master = pd.read_excel(xl_path, sheet_name="Model_Master").set_index("model_code").to_dict("index")
            truck = pd.read_excel(xl_path, sheet_name="Truck_Master").set_index("truck_type").to_dict("index")["26ft"]

            for scenario_name, scenario in self.BASELINE.items():
                # Apply calibrations
                for model, cal in scenario["calibrations"].items():
                    if model in master:
                        master[model].update(cal)

                result = simulate(scenario["orders"], master, truck)
                expected = scenario["expected"]

                # Check fits
                if result["fits"] == expected["fits"] and result["fitted_count"] == expected["fitted_count"]:
                    report.checks.append(CheckResult(
                        name=f"{scenario_name}__fitted",
                        status=CheckStatus.PASS,
                        message=f"All {expected['fitted_count']} units fit as expected",
                    ))
                else:
                    report.checks.append(CheckResult(
                        name=f"{scenario_name}__fitted",
                        status=CheckStatus.FAIL,
                        expected=f"fits={expected['fits']}, {expected['fitted_count']} units",
                        actual=f"fits={result['fits']}, {result['fitted_count']} units",
                        message="Fitted count regression — algorithm is leaving units out",
                        fix_hint="Check calibrations and pair-packing logic in engine/best_packer.py",
                    ))

                # Check compactness
                x_used = result["metrics"]["x_used_ft"]
                comp = result["metrics"]["compactness_pct"]
                if x_used <= expected["x_used_ft_max"] and comp <= expected["compactness_pct_max"]:
                    report.checks.append(CheckResult(
                        name=f"{scenario_name}__compactness",
                        status=CheckStatus.PASS,
                        message=f"Compactness OK: {x_used} ft used ({comp}%)",
                    ))
                else:
                    report.checks.append(CheckResult(
                        name=f"{scenario_name}__compactness",
                        status=CheckStatus.FAIL,
                        expected=f"≤{expected['x_used_ft_max']} ft, ≤{expected['compactness_pct_max']}%",
                        actual=f"{x_used} ft, {comp}%",
                        message="Compactness regression — pair-packing not achieving target",
                    ))

                # Check lane utilization per model
                by_model = {}
                for p in result["placements"]:
                    by_model.setdefault(p["model_code"], []).append(p)
                for model, expected_lanes in expected["lane_count"].items():
                    if model not in by_model:
                        continue
                    actual_lanes = len(set(p["lane"] for p in by_model[model]))
                    if actual_lanes == expected_lanes:
                        report.checks.append(CheckResult(
                            name=f"{scenario_name}__lanes_{model}",
                            status=CheckStatus.PASS,
                            message=f"{model}: {actual_lanes} lanes (max achieved)",
                        ))
                    else:
                        report.checks.append(CheckResult(
                            name=f"{scenario_name}__lanes_{model}",
                            status=CheckStatus.FAIL,
                            expected=expected_lanes,
                            actual=actual_lanes,
                            message=f"{model} not using max lane count",
                            fix_hint="Check _lane_pack_group in best_packer.py — n_lanes calculation",
                        ))

        except Exception as e:
            report.checks.append(CheckResult(
                name="algorithm_run",
                status=CheckStatus.FAIL,
                message=f"Algorithm validation failed to run: {e}",
            ))

        return report


# =============================================================================
# Agent 3: UI Validator
# Detects: app.py drift from DESIGN_SPEC.md
# =============================================================================

class UIValidator:
    """
    Validates that app.py implements features documented in DESIGN_SPEC.md.

    Catches missing UI elements like:
      - Email panel not integrated
      - PDF download button missing
      - Expert panel review section missing
      - Hardcoded units (mm/kg instead of ft/lb)
    """

    name = "UI Validator"

    # Required features per DESIGN_SPEC.md
    REQUIRED_FEATURES = {
        "email_panel": {
            "tokens": ["email_ui", "render_email_panel"],
            "fix": "Add 'from engine.email_ui import render_email_panel' and call it after simulation results.",
        },
        "us_units_display": {
            "tokens": ["ft", "lb", "ft³", "ft^3", "feet", "pounds"],
            "fix": "Display dimensions in feet/inches and weight in pounds (mm/kg internally only).",
            "at_least_n": 2,
        },
        "load_volume_kpi": {
            "tokens": ["volume", "ft³", "ft^3", "cubic"],
            "fix": "Show Load Volume as a KPI card (not just weight + length).",
            "at_least_n": 1,
        },
        "load_rate_3way": {
            "tokens": ["length", "volume", "weight"],
            "fix": "Show 3-way load rate: Length %, Volume %, Weight % per design spec.",
            "at_least_n": 3,
        },
    }

    def validate(self, project_root: Path) -> ValidationReport:
        report = ValidationReport(agent=self.name)

        try:
            app_py = (project_root / "app.py").read_text().lower()

            for feature, spec in self.REQUIRED_FEATURES.items():
                found_count = sum(1 for token in spec["tokens"] if token.lower() in app_py)
                threshold = spec.get("at_least_n", 1)

                if found_count >= threshold:
                    report.checks.append(CheckResult(
                        name=f"ui_{feature}",
                        status=CheckStatus.PASS,
                        message=f"{feature}: implemented ({found_count} references found)",
                    ))
                else:
                    report.checks.append(CheckResult(
                        name=f"ui_{feature}",
                        status=CheckStatus.FAIL,
                        expected=f"≥{threshold} references to: {spec['tokens']}",
                        actual=f"{found_count} found",
                        message=f"UI feature missing: {feature}",
                        fix_hint=spec["fix"],
                    ))

            # Check that mockup screens are documented
            spec_path = project_root / "docs" / "DESIGN_SPEC.md"
            if spec_path.exists():
                report.checks.append(CheckResult(
                    name="design_spec_exists",
                    status=CheckStatus.PASS,
                    message="docs/DESIGN_SPEC.md exists — Claude Code can reference it",
                ))
            else:
                report.checks.append(CheckResult(
                    name="design_spec_exists",
                    status=CheckStatus.FAIL,
                    message="docs/DESIGN_SPEC.md missing — Claude Code has no UI reference",
                    fix_hint="Create docs/DESIGN_SPEC.md with mockup specifications",
                ))

        except Exception as e:
            report.checks.append(CheckResult(
                name="ui_validation",
                status=CheckStatus.FAIL,
                message=f"UI validation failed: {e}",
            ))

        return report


# =============================================================================
# Orchestrator
# =============================================================================

class ValidationPanel:
    """Runs all validation agents and reports results."""

    def __init__(self):
        self.agents = [
            SpecValidator(),
            AlgorithmValidator(),
            UIValidator(),
        ]

    def run(self, project_root: Path) -> Dict[str, Any]:
        reports = [agent.validate(project_root) for agent in self.agents]
        return {
            "all_passed": all(r.all_passed for r in reports),
            "total_passed": sum(r.passed for r in reports),
            "total_failed": sum(r.failed for r in reports),
            "reports": reports,
        }

    def print_report(self, result: Dict[str, Any]) -> None:
        print("=" * 70)
        print("CLAUDE CODE VALIDATION PANEL")
        print("=" * 70)

        for report in result["reports"]:
            print(f"\n── {report.agent} ──")
            print(f"  {report.passed} passed, {report.failed} failed\n")
            for check in report.checks:
                print(f"  {check.status.value} {check.name}")
                if check.message:
                    print(f"      {check.message}")
                if check.status == CheckStatus.FAIL:
                    if check.expected is not None:
                        print(f"      Expected: {check.expected}")
                        print(f"      Actual:   {check.actual}")
                    if check.fix_hint:
                        print(f"      💡 Fix: {check.fix_hint}")

        print("\n" + "=" * 70)
        total = result["total_passed"] + result["total_failed"]
        if result["all_passed"]:
            print(f"✅ ALL PASSED — {result['total_passed']}/{total}")
        else:
            print(f"❌ FAILURES — {result['total_passed']}/{total} passed, "
                  f"{result['total_failed']} need attention")
        print("=" * 70)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run validation agents")
    parser.add_argument("--project", default=".", type=Path, help="Project root")
    parser.add_argument("--agent", choices=["spec", "algorithm", "ui", "all"], default="all")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text")
    args = parser.parse_args()

    panel = ValidationPanel()
    if args.agent != "all":
        panel.agents = [a for a in panel.agents
                        if args.agent.lower() in a.name.lower()]

    result = panel.run(args.project.resolve())

    if args.json:
        output = {
            "all_passed": result["all_passed"],
            "total_passed": result["total_passed"],
            "total_failed": result["total_failed"],
            "reports": [
                {
                    "agent": r.agent,
                    "checks": [
                        {"name": c.name, "status": c.status.name, "message": c.message,
                         "expected": str(c.expected) if c.expected else None,
                         "actual": str(c.actual) if c.actual else None,
                         "fix_hint": c.fix_hint}
                        for c in r.checks
                    ],
                }
                for r in result["reports"]
            ],
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        panel.print_report(result)

    sys.exit(0 if result["all_passed"] else 1)


if __name__ == "__main__":
    main()
