"""
Certificate Eligibility Checker — with per-subject waiver, double-major/minor, and course-department support.

Serves an HTML web interface and provides a REST API for eligibility checking.

Courses taken format: list of {"name": "course_name", "department": "dept_name"}
External credits exclude: student_dept + double_major_depts + minor_depts
"""

import json
import hashlib
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import argparse

RULES_DIR = Path(__file__).parent / "rules"
HTML_DIR = Path(__file__).parent


def load_all_programs() -> list[dict]:
    """Load all program JSON files."""
    programs = []
    if not RULES_DIR.exists():
        return programs
    for f in sorted(RULES_DIR.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)
            programs.append(data)
        except Exception:
            continue
    return programs


def resolve_credits(credits_value) -> int:
    """Resolve credits to an integer."""
    if isinstance(credits_value, (int, float)):
        return int(credits_value)
    s = str(credits_value).strip()
    if "-" in s:
        parts = s.split("-")
        return int(parts[-1])
    if "依" in s or "規定" in s:
        return 3
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 3


def check_eligibility(
    program_id: str,
    academic_year: int,
    semester: int | None,
    student_dept: str,
    courses_taken: list[dict],
    waivers: dict[str, list[str]],
    double_major_depts: list[str] | None = None,
    minor_depts: list[str] | None = None,
) -> dict:
    """Check eligibility with per-subject waiver, double-major, and course-department support.

    courses_taken: list of {"name": "course_name", "department": "dept_name"}
    double_major_depts: list of departments that are the student's double major
    minor_depts: list of departments that are the student's minor
    waivers: dict mapping subject program_subject -> list of waiver condition IDs toggled on
    """
    # Find program
    programs = load_all_programs()
    program = None
    for p in programs:
        if p["program_id"] == program_id:
            program = p
            break

    if not program:
        return {"error": f"Program '{program_id}' not found"}

    # Find version
    versions = program.get("versions", [])
    version = None

    matching = [v for v in versions if v["academic_year"] == academic_year]
    if matching:
        if semester:
            exact = [v for v in matching if v.get("semester") == semester]
            if exact:
                version = exact[0]
        if not version:
            matching.sort(key=lambda v: v.get("semester", 0), reverse=True)
            version = matching[0]
    else:
        candidates = [v for v in versions if v["academic_year"] <= academic_year]
        if candidates:
            candidates.sort(key=lambda v: (v["academic_year"], v.get("semester", 0)), reverse=True)
            version = candidates[0]

    if not version:
        return {"error": f"No version found for year {academic_year}"}

    # Build "own departments" set for external credit calculation
    own_depts = {student_dept}
    if double_major_depts:
        own_depts.update(d for d in double_major_depts if d)
    if minor_depts:
        own_depts.update(d for d in minor_depts if d)

    # Build course lookup: {(name, dept)} for fast matching
    # Also build name-only set as fallback
    course_by_name_dept = {}
    course_names = set()
    for c in courses_taken:
        name = c.get("name", c) if isinstance(c, dict) else c
        dept = c.get("department", "") if isinstance(c, dict) else ""
        course_by_name_dept[(name, dept)] = True
        course_names.add(name)

    result = {
        "program_name": program["program_name"],
        "program_id": program_id,
        "academic_year": version["academic_year"],
        "semester": version.get("semester"),
        "student_department": student_dept,
        "double_major_depts": double_major_depts or [],
        "minor_depts": minor_depts or [],
        "own_departments": sorted(own_depts),
        "courses_taken": courses_taken,
        "waivers": waivers,
        "groups": [],
        "total_credits_earned": 0,
        "total_credits_required": version["requirements"]["total_min_credits"],
        "external_credits_earned": 0,
        "external_credits_required": version["requirements"]["external_credits"]["min"],
        "tag_credits": {},
        "eligible": False,
        "summary": "",
        "unmet_requirements": [],
    }

    # Track tag credits across groups
    tag_credits = {}

    for group in version["course_groups"]:
        group_result = check_group(group, course_names, course_by_name_dept, own_depts, waivers)
        result["groups"].append(group_result)
        result["total_credits_earned"] += group_result["credits_earned"]
        result["external_credits_earned"] += group_result["external_credits_earned"]

        # Aggregate tag credits
        for tag, credits in group_result.get("tag_credits_earned", {}).items():
            tag_credits[tag] = tag_credits.get(tag, 0) + credits

    result["tag_credits"] = tag_credits

    # Check required_tags within this version's groups
    required_tags = []
    for group in version["course_groups"]:
        req = group.get("credit_requirement", {}).get("required_tags", [])
        required_tags.extend(req)

    tags_met = True
    tag_details = []
    for req in required_tags:
        tag = req["tag"]
        earned = tag_credits.get(tag, 0)
        needed = req["min_credits"]
        met = earned >= needed
        if not met:
            tags_met = False
        tag_details.append({"tag": tag, "earned": earned, "required": needed, "met": met})

    result["tag_details"] = tag_details

    # Check external credits
    external_req = version["requirements"]["external_credits"]
    exclude_double_major = external_req.get("exclude_double_major", True)
    exclude_minor = external_req.get("exclude_minor", True)
    external_met = result["external_credits_earned"] >= result["external_credits_required"]

    total_met = result["total_credits_earned"] >= result["total_credits_required"]
    all_groups_met = all(g["is_met"] for g in result["groups"])
    non_course_reqs = version["requirements"].get("non_course_requirements", [])
    non_course_met = len(non_course_reqs) == 0

    result["eligible"] = total_met and external_met and all_groups_met and non_course_met and tags_met

    if result["eligible"]:
        result["summary"] = f"✅ 符合「{program['program_name']}」證書資格！"
    else:
        parts = []
        if not total_met:
            deficit = result["total_credits_required"] - result["total_credits_earned"]
            parts.append(f"總學分不足 {deficit} 學分（已修 {result['total_credits_earned']}/{result['total_credits_required']}）")
        if not external_met:
            deficit = result["external_credits_required"] - result["external_credits_earned"]
            parts.append(f"外系學分不足 {deficit} 學分（已修 {result['external_credits_earned']}/{result['external_credits_required']}，不含本系{', '.join(own_depts)}）")
        if not tags_met:
            for td in tag_details:
                if not td["met"]:
                    parts.append(f"＊號選修不足 {td['required']} 學分（已修 {td['earned']} 學分）")
        if not all_groups_met:
            unmet = [g["label"] for g in result["groups"] if not g["is_met"]]
            parts.append(f"未滿足：{', '.join(unmet)}")
        result["summary"] = f"❌ 尚未符合「{program['program_name']}」證書資格。{'；'.join(parts)}"
        result["unmet_requirements"] = parts

    return result


def check_group(group, course_names, course_by_name_dept, own_depts, waivers):
    """Check a course group with department-aware matching."""
    rule = group["selection_rule"]
    min_credits = group["credit_requirement"]["min"]

    result = {
        "id": group["id"],
        "label": group["label"],
        "selection_rule": rule,
        "credits_required": min_credits,
        "credits_earned": 0,
        "external_credits_earned": 0,
        "subjects_taken": [],
        "subjects_missing": [],
        "is_met": False,
        "tag_credits_earned": {},
    }

    subjects_satisfied = 0

    for subject in group["subjects"]:
        subject_result = check_subject(subject, course_names, course_by_name_dept, own_depts, waivers)
        if subject_result["satisfied"]:
            subjects_satisfied += 1
            result["credits_earned"] += subject_result["credits"]
            result["subjects_taken"].append(subject_result)
            if not subject_result["is_own_dept"]:
                result["external_credits_earned"] += subject_result["credits"]

            # Track tag credits
            tags = subject.get("tags", [])
            for tag in tags:
                result["tag_credits_earned"][tag] = result["tag_credits_earned"].get(tag, 0) + subject_result["credits"]
        else:
            result["subjects_missing"].append(subject_result)

    if rule["type"] == "all":
        result["is_met"] = subjects_satisfied == len(group["subjects"]) and result["credits_earned"] >= min_credits
    elif rule["type"] == "pick_n":
        result["is_met"] = subjects_satisfied >= rule["pick"] and result["credits_earned"] >= min_credits
    elif rule["type"] == "min_credits":
        result["is_met"] = result["credits_earned"] >= min_credits
    else:
        result["is_met"] = result["credits_earned"] >= min_credits

    return result


def check_subject(subject, course_names, course_by_name_dept, own_depts, waivers):
    """Check if a subject is satisfied with department-aware course matching.

    A course taken from a department NOT listed in the alternative's departments
    will NOT satisfy the requirement. For example, if "物件導向程式設計" is only
    offered by 資工系 and 資管系, taking it from 電機系 won't count.
    """
    program_subject = subject["program_subject"]

    # Track courses where name matches but department doesn't (for UI hints)
    department_mismatches = []

    # Check if any alternative course is taken — prefer department-specific match
    best_match = None
    for alt in subject["alternatives"]:
        if alt["name"] not in course_names:
            continue

        credits = resolve_credits(alt["credits"])
        alt_depts = alt.get("departments", [])

        # Find the department the student took this course from
        matched_dept = None
        for c_name, c_dept in course_by_name_dept:
            if c_name == alt["name"]:
                matched_dept = c_dept
                break

        # Department validation: if the alternative specifies departments
        # and the student provided a non-empty department,
        # the student's department must be one of the alternative's departments
        if alt_depts and matched_dept and matched_dept != "":
            if matched_dept not in alt_depts:
                department_mismatches.append({
                    "name": alt["name"],
                    "taken_dept": matched_dept,
                    "valid_depts": alt_depts,
                })
                continue  # Skip this alternative — wrong department

        # Determine if this counts as own-dept credit
        if matched_dept and matched_dept != "":
            is_own = matched_dept in own_depts
        else:
            # No department info from course — check if any offering dept is "own"
            is_own = any(d in own_depts for d in alt_depts)

        if best_match is None or (not best_match[2] and is_own):
            # Prefer matches that count as own-dept (more favorable)
            best_match = (alt, credits, is_own, alt_depts)

    if best_match:
        alt, credits, is_own, alt_depts = best_match
        return {
            "subject": program_subject,
            "satisfied": True,
            "satisfied_by": alt["name"],
            "satisfied_type": "course",
            "credits": credits,
            "is_own_dept": is_own,
            "department": alt_depts,
            "waiver_options": get_waiver_options(subject),
        }

    # Check waiver
    subject_waivers = waivers.get(program_subject, [])
    waiver_data = subject.get("waiver", {})
    if waiver_data and waiver_data.get("allowed") and subject_waivers:
        for wa in waiver_data.get("waiver_alternatives", []):
            wa_id = make_waiver_id(program_subject, wa["condition"])
            if wa_id in subject_waivers:
                return {
                    "subject": program_subject,
                    "satisfied": True,
                    "satisfied_by": f"抵免：{wa['condition']}",
                    "satisfied_type": "waiver",
                    "credits": wa.get("credits_granted", 0),
                    "is_own_dept": False,
                    "department": [],
                    "waiver_note": wa.get("note", ""),
                }

    waiver_options = get_waiver_options(subject)
    result = {
        "subject": program_subject,
        "satisfied": False,
        "satisfied_by": None,
        "satisfied_type": None,
        "credits": 0,
        "is_own_dept": False,
        "alternatives": [alt["name"] for alt in subject["alternatives"]],
        "alternative_departments": {alt["name"]: alt.get("departments", []) for alt in subject["alternatives"]},
        "waiver_options": waiver_options,
    }
    if department_mismatches:
        result["department_mismatches"] = department_mismatches
    return result


def get_waiver_options(subject):
    waiver = subject.get("waiver", {})
    if not waiver or not waiver.get("allowed"):
        return []
    options = []
    for wa in waiver.get("waiver_alternatives", []):
        options.append({
            "id": make_waiver_id(subject["program_subject"], wa["condition"]),
            "condition": wa["condition"],
            "credits_granted": wa.get("credits_granted", 0),
            "note": wa.get("note", ""),
        })
    return options


def make_waiver_id(subject, condition):
    h = hashlib.md5(f"{subject}:{condition}".encode()).hexdigest()[:8]
    return f"waiver_{h}"


class EligibilityHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self.serve_html()
        elif path == "/api/programs":
            self.serve_programs()
        elif path == "/api/check":
            params = parse_qs(parsed.query)
            self.serve_check(params)
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/check":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self.send_json({"error": "Invalid JSON"}, status=400)
                return
            result = check_eligibility(
                program_id=data.get("program", ""),
                academic_year=data.get("year", 113),
                semester=data.get("semester"),
                student_dept=data.get("dept", ""),
                courses_taken=data.get("courses", []),
                waivers=data.get("waivers", {}),
                double_major_depts=data.get("double_major_depts", []),
                minor_depts=data.get("minor_depts", []),
            )
            self.send_json(result)
        else:
            self.send_error(404)

    def serve_html(self):
        html_path = HTML_DIR / "index.html"
        if html_path.exists():
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            with open(html_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_error(404, "index.html not found")

    def serve_programs(self):
        programs = load_all_programs()
        result = []
        for p in programs:
            years = sorted(set(v["academic_year"] for v in p.get("versions", [])))
            result.append({
                "id": p["program_id"],
                "name": p["program_name"],
                "type": p.get("program_type", ""),
                "years": years,
                "is_discontinued": p.get("is_discontinued", False),
            })
        self.send_json(result)

    def serve_check(self, params):
        # Support both GET (legacy) and prepare for POST
        program_id = params.get("program", [""])[0]
        year = int(params.get("year", ["113"])[0])
        dept = params.get("dept", [""])[0]
        courses_json = params.get("courses_json", ["[]"])[0]
        waivers_str = params.get("waivers", ["{}"])[0]
        double_major_str = params.get("double_major", [""])[0]
        minor_str = params.get("minor", [""])[0]

        try:
            courses = json.loads(courses_json)
        except json.JSONDecodeError:
            courses = []

        try:
            waivers = json.loads(waivers_str)
        except json.JSONDecodeError:
            waivers = {}

        double_major_depts = [d.strip() for d in double_major_str.split(",") if d.strip()] if double_major_str else []
        minor_depts = [d.strip() for d in minor_str.split(",") if d.strip()] if minor_str else []

        result = check_eligibility(program_id, year, None, dept, courses, waivers, double_major_depts, minor_depts)
        self.send_json(result)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Certificate Eligibility Checker Server")
    parser.add_argument("--port", type=int, default=8080, help="Port to serve on")
    args = parser.parse_args()

    server = HTTPServer(("localhost", args.port), EligibilityHandler)
    import sys
    if sys.stdout.encoding != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print(f"Certificate Eligibility Checker running at http://localhost:{args.port}")
    print(f"   Rules directory: {RULES_DIR}")
    print(f"   Available programs: {len(load_all_programs())}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()