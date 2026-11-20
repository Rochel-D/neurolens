import pytest

from backend.app.services import lab_parser
from tests.conftest import sample


def by_id(results):
    return {r.test_id: r for r in lab_parser.parse_report(results)} if isinstance(results, str) else {r.test_id: r for r in results}


def test_cbc_values_units_and_status():
    r = by_id(sample("cbc_report.txt").decode())
    assert r["hemoglobin"].value == 10.4 and r["hemoglobin"].status == "low"
    assert r["wbc"].value == 7.8 and r["wbc"].ref_low == 4.0  # cells/cumm converted to ×10³/µL
    assert r["platelets"].value == 260 and r["platelets"].status == "normal"  # lakh/cumm converted
    assert r["mchc"].status == "low" and "hemoglobin" in r  # MCHC not confused with haemoglobin


def test_one_sided_ranges_and_ratio_lines_ignored():
    r = by_id(sample("metabolic_report.txt").decode())
    assert r["hdl"].ref_low == 40 and r["hdl"].ref_high is None and r["hdl"].status == "low"
    assert r["ldl"].ref_high == 100 and r["ldl"].status == "high"
    assert r["cholesterol_total"].value == 232  # not the ratio line


def test_critical_value_detected():
    r = by_id(sample("thyroid_electrolytes_report.txt").decode())
    assert r["potassium"].status == "critical_high"
    assert r["egfr"].range_source == "standard"


def test_unit_conversion_mmol_glucose():
    res = lab_parser.parse_report("Fasting Blood Glucose  6.5 mmol/L")
    assert res[0].value == pytest.approx(117, abs=0.1) and res[0].status == "high"


def test_sex_specific_standard_range():
    m = lab_parser.parse_report("Haemoglobin 13.0 g/dL", sex="male")[0]
    f = lab_parser.parse_report("Haemoglobin 13.0 g/dL", sex="female")[0]
    assert m.status == "low" and f.status == "normal"


def test_pdf_magic_bytes_and_encrypted_rejected():
    with pytest.raises(lab_parser.ReportError):
        lab_parser.extract_text(b"not a pdf", "fake.pdf")
    with pytest.raises(lab_parser.ReportError):
        lab_parser.extract_text(b"x" * (11 * 1024 * 1024), "big.txt", max_mb=10)


def test_real_pdf_report():
    from pathlib import Path

    pdf = Path(__file__).resolve().parents[1] / "sample_data" / "reports" / "metabolic_report.pdf"
    text = lab_parser.extract_text(pdf.read_bytes(), "metabolic_report.pdf")
    assert {"ldl", "hba1c", "alt"} <= {r.test_id for r in lab_parser.parse_report(text)}


def test_digit_grouping_regression():
    """Bug found in manual testing: '2,00,000' was read as 2.00 → platelets 0.002 ×10³/µL (false critical)."""
    p = lab_parser.parse_line("Platelet Count 2,00,000 /cumm 1,50,000 - 4,10,000")
    assert (p.value, p.ref_low, p.ref_high, p.status) == (200, 150, 410, "normal")
    assert lab_parser.parse_line("Platelet Count 250,000 /cumm 150,000 - 410,000").value == 250
    assert lab_parser.parse_line("Platelet Count 150000 cumm 150000 - 410000").status == "normal"
    assert lab_parser.parse_line("Haemoglobin 13,5 g/dL 12,0 - 15,5").value == 13.5  # decimal comma still works


def test_lakh_with_spaces_and_mixed_units_v21():
    """v2 broken window: 'Platelet Count 2 Lakhs /cumm 150000 - 410000' was read as 0.002 ×10³/µL (false critical)."""
    p = lab_parser.parse_line("Platelet Count 2 Lakhs /cumm 150000 - 410000")
    assert (p.value, p.ref_low, p.ref_high, p.status, p.suspect) == (200, 150, 410, "normal", False)
    assert lab_parser.parse_line("Platelet count 15 x10^3/uL 150 - 410").status == "critical_low"  # real low count still critical


def test_implausible_value_is_suspect_not_critical_alarm():
    p = lab_parser.parse_line("Platelet count 2 /cumm 150000-410000")
    assert p.suspect and lab_parser.summarise([p])["suspect_tests"] == ["Platelet count"]
