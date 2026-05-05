/**
 * Certificate Eligibility Checker — Pure JavaScript implementation
 *
 * Ported from checker.py. Pure functions with no DOM or fetch dependencies.
 * Designed for easy porting to Dart.
 *
 * Usage:
 *   const result = checkEligibility(program, academicYear, studentDept, coursesTaken, waivers, doubleMajorDepts, minorDepts);
 */

// ─── Credits ────────────────────────────────────────────────────────────────

/**
 * Resolve a credits value to an integer.
 * Handles: integers, floats, strings like "3", "2-3" (use max), "依規定" (default 3).
 */
function resolveCredits(creditsValue) {
  if (typeof creditsValue === 'number') {
    return Math.round(creditsValue);
  }
  const s = String(creditsValue).trim();
  if (s.includes('-')) {
    const parts = s.split('-');
    return parseInt(parts[parts.length - 1], 10);
  }
  if (s.includes('依') || s.includes('規定')) {
    return 3;
  }
  const parsed = parseFloat(s);
  if (isNaN(parsed)) return 3;
  return Math.round(parsed);
}

// ─── Waiver ─────────────────────────────────────────────────────────────────

/**
 * Generate a waiver ID from subject and condition.
 */
function makeWaiverId(subject, condition) {
  let hash = 0;
  const str = `${subject}:${condition}`;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash |= 0; // Convert to 32bit integer
  }
  return `waiver_${Math.abs(hash).toString(16).slice(0, 8)}`;
}

/**
 * Get waiver options for a subject.
 */
function getWaiverOptions(subject) {
  const waiver = subject.waiver;
  if (!waiver || !waiver.allowed) return [];
  return (waiver.waiver_alternatives || []).map(wa => ({
    id: makeWaiverId(subject.program_subject, wa.condition),
    condition: wa.condition,
    credits_granted: wa.credits_granted || 0,
    note: wa.note || '',
  }));
}

// ─── Department Matching ────────────────────────────────────────────────────

/**
 * Check if a student's course department is valid for an alternative's departments.
 *
 * Rules:
 * - departments: [] → any department is valid (校內各系, 各開課系所)
 * - departments containing "跨院選修" → any department is valid (跨院選修 matches all)
 * - Otherwise, student's department must be in the departments list
 * - If student provides no department (empty string), lenient: still counts
 */
function isDepartmentValid(studentDept, alternativeDepts) {
  // No department info from student → lenient match
  if (!studentDept || studentDept === '') return true;

  // Empty departments array → any department valid (各開課系所, 校內各系)
  if (!alternativeDepts || alternativeDepts.length === 0) return true;

  // If any dept in the list contains "跨院選修" → any department valid
  if (alternativeDepts.some(d => d.includes('跨院選修'))) return true;

  // Otherwise, student's dept must be in the list
  return alternativeDepts.includes(studentDept);
}

// ─── Subject Checking ────────────────────────────────────────────────────────

/**
 * Check if a subject is satisfied with department-aware course matching.
 *
 * @param {Object} subject - The subject definition from program rules
 * @param {Set<string>} courseNames - Set of course names the student has taken
 * @param {Map<string, string>} courseByNameDept - Map of (courseName → department) for taken courses
 * @param {Set<string>} ownDepts - Set of student's own departments (dept + double major + minor)
 * @param {Object} waivers - Dict mapping subject → list of waiver condition IDs
 * @returns {Object} Subject result
 */
function checkSubject(subject, courseNames, courseByNameDept, ownDepts, waivers) {
  const programSubject = subject.program_subject;

  // Track courses where name matches but department doesn't
  const departmentMismatches = [];

  // Check if any alternative course is taken — prefer department-specific match
  let bestMatch = null;

  for (const alt of subject.alternatives) {
    if (!courseNames.has(alt.name)) continue;

    const credits = resolveCredits(alt.credits);
    const altDepts = alt.departments || [];

    // Find the department the student took this course from
    let matchedDept = courseByNameDept.get(alt.name) || '';

    // Department validation
    if (!isDepartmentValid(matchedDept, altDepts)) {
      departmentMismatches.push({
        name: alt.name,
        taken_dept: matchedDept,
        valid_depts: altDepts,
      });
      continue; // Skip this alternative — wrong department
    }

    // Determine if this counts as own-dept credit
    let isOwn;
    if (matchedDept && matchedDept !== '') {
      isOwn = ownDepts.has(matchedDept);
    } else {
      // No department info from course — check if any offering dept is "own"
      isOwn = altDepts.some(d => ownDepts.has(d));
    }

    // Prefer matches that count as own-dept (more favorable for the student)
    if (bestMatch === null || (!bestMatch.isOwn && isOwn)) {
      bestMatch = { alt, credits, isOwn, altDepts };
    }
  }

  if (bestMatch) {
    return {
      subject: programSubject,
      satisfied: true,
      satisfied_by: bestMatch.alt.name,
      satisfied_type: 'course',
      credits: bestMatch.credits,
      is_own_dept: bestMatch.isOwn,
      department: bestMatch.altDepts,
      waiver_options: getWaiverOptions(subject),
      tags: subject.tags || [],
    };
  }

  // Check waiver
  const subjectWaivers = waivers[programSubject] || [];
  const waiverData = subject.waiver;
  if (waiverData && waiverData.allowed && subjectWaivers.length > 0) {
    for (const wa of (waiverData.waiver_alternatives || [])) {
      const waId = makeWaiverId(programSubject, wa.condition);
      if (subjectWaivers.includes(waId)) {
        return {
          subject: programSubject,
          satisfied: true,
          satisfied_by: `抵免：${wa.condition}`,
          satisfied_type: 'waiver',
          credits: wa.credits_granted || 0,
          is_own_dept: false,
          department: [],
          waiver_note: wa.note || '',
          tags: subject.tags || [],
        };
      }
    }
  }

  const waiverOptions = getWaiverOptions(subject);
  const result = {
    subject: programSubject,
    satisfied: false,
    satisfied_by: null,
    satisfied_type: null,
    credits: 0,
    is_own_dept: false,
    alternatives: subject.alternatives.map(a => a.name),
    alternative_departments: Object.fromEntries(
      subject.alternatives.map(a => [a.name, a.departments || []])
    ),
    waiver_options: waiverOptions,
    tags: subject.tags || [],
  };
  if (departmentMismatches.length > 0) {
    result.department_mismatches = departmentMismatches;
  }
  return result;
}

// ─── Group Checking ──────────────────────────────────────────────────────────

/**
 * Check a course group with department-aware matching.
 */
function checkGroup(group, courseNames, courseByNameDept, ownDepts, waivers) {
  const rule = group.selection_rule;
  const minCredits = group.credit_requirement.min;

  const result = {
    id: group.id,
    label: group.label,
    selection_rule: rule,
    credits_required: minCredits,
    credits_earned: 0,
    external_credits_earned: 0,
    subjects_taken: [],
    subjects_missing: [],
    is_met: false,
    tag_credits_earned: {},
  };

  let subjectsSatisfied = 0;

  for (const subject of group.subjects) {
    const subjectResult = checkSubject(subject, courseNames, courseByNameDept, ownDepts, waivers);
    if (subjectResult.satisfied) {
      subjectsSatisfied += 1;
      result.credits_earned += subjectResult.credits;
      result.subjects_taken.push(subjectResult);
      if (!subjectResult.is_own_dept) {
        result.external_credits_earned += subjectResult.credits;
      }

      // Track tag credits
      const tags = subject.tags || [];
      for (const tag of tags) {
        result.tag_credits_earned[tag] = (result.tag_credits_earned[tag] || 0) + subjectResult.credits;
      }
    } else {
      result.subjects_missing.push(subjectResult);
    }
  }

  if (rule.type === 'all') {
    result.is_met = subjectsSatisfied === group.subjects.length && result.credits_earned >= minCredits;
  } else if (rule.type === 'pick_n') {
    result.is_met = subjectsSatisfied >= rule.pick && result.credits_earned >= minCredits;
  } else if (rule.type === 'min_credits') {
    result.is_met = result.credits_earned >= minCredits;
  } else {
    result.is_met = result.credits_earned >= minCredits;
  }

  return result;
}

// ─── Main Eligibility Check ──────────────────────────────────────────────────

/**
 * Check eligibility for a program certificate.
 *
 * @param {Object} program - The full program JSON object (from rules/*.json)
 * @param {number} academicYear - The academic year to check against
 * @param {number|null} semester - The semester (optional)
 * @param {string} studentDept - Student's department
 * @param {Array<{name: string, department: string}>} coursesTaken - Courses taken
 * @param {Object} waivers - Dict mapping subject → list of waiver condition IDs
 * @param {string[]} doubleMajorDepts - Double major departments
 * @param {string[]} minorDepts - Minor departments
 * @returns {Object} Eligibility result
 */
function checkEligibility(program, academicYear, semester, studentDept, coursesTaken, waivers, doubleMajorDepts, minorDepts) {
  // Find version
  const versions = program.versions || [];
  let version = null;

  const matching = versions.filter(v => v.academic_year === academicYear);
  if (matching.length > 0) {
    if (semester) {
      const exact = matching.filter(v => v.semester === semester);
      if (exact.length > 0) version = exact[0];
    }
    if (!version) {
      const sorted = [...matching].sort((a, b) => (b.semester || 0) - (a.semester || 0));
      version = sorted[0];
    }
  } else {
    const candidates = versions.filter(v => v.academic_year <= academicYear);
    if (candidates.length > 0) {
      candidates.sort((a, b) => {
        if (b.academic_year !== a.academic_year) return b.academic_year - a.academic_year;
        return (b.semester || 0) - (a.semester || 0);
      });
      version = candidates[0];
    }
  }

  if (!version) {
    return { error: `No version found for year ${academicYear}` };
  }

  // Build "own departments" set for external credit calculation
  const ownDepts = new Set([studentDept]);
  if (doubleMajorDepts) {
    for (const d of doubleMajorDepts) {
      if (d) ownDepts.add(d);
    }
  }
  if (minorDepts) {
    for (const d of minorDepts) {
      if (d) ownDepts.add(d);
    }
  }

  // Build course lookup: Map<name, dept> for fast matching
  const courseByNameDept = new Map();
  const courseNames = new Set();
  for (const c of coursesTaken) {
    const name = typeof c === 'object' ? (c.name || '') : c;
    const dept = typeof c === 'object' ? (c.department || '') : '';
    courseByNameDept.set(name, dept);
    courseNames.add(name);
  }

  const result = {
    program_name: program.program_name,
    program_id: program.program_id,
    academic_year: version.academic_year,
    semester: version.semester || null,
    student_department: studentDept,
    double_major_depts: doubleMajorDepts || [],
    minor_depts: minorDepts || [],
    own_departments: [...ownDepts].sort(),
    courses_taken: coursesTaken,
    waivers: waivers,
    groups: [],
    total_credits_earned: 0,
    total_credits_required: version.requirements.total_min_credits,
    external_credits_earned: 0,
    external_credits_required: version.requirements.external_credits.min,
    tag_credits: {},
    eligible: false,
    summary: '',
    unmet_requirements: [],
  };

  // Track tag credits across groups
  const tagCredits = {};

  for (const group of version.course_groups) {
    const groupResult = checkGroup(group, courseNames, courseByNameDept, ownDepts, waivers);
    result.groups.push(groupResult);
    result.total_credits_earned += groupResult.credits_earned;
    result.external_credits_earned += groupResult.external_credits_earned;

    // Aggregate tag credits
    for (const [tag, credits] of Object.entries(groupResult.tag_credits_earned || {})) {
      tagCredits[tag] = (tagCredits[tag] || 0) + credits;
    }
  }

  result.tag_credits = tagCredits;

  // Check required tags within this version's groups
  const requiredTags = [];
  for (const group of version.course_groups) {
    const req = group.credit_requirement?.required_tags;
    if (req) {
      requiredTags.push(...req);
    }
  }

  let tagsMet = true;
  const tagDetails = [];
  for (const req of requiredTags) {
    const tag = req.tag;
    const earned = tagCredits[tag] || 0;
    const needed = req.min_credits;
    const met = earned >= needed;
    if (!met) tagsMet = false;
    tagDetails.push({ tag, earned, required: needed, met });
  }

  result.tag_details = tagDetails;

  // Check external credits
  const externalReq = version.requirements.external_credits;
  const externalMet = result.external_credits_earned >= result.external_credits_required;

  const totalMet = result.total_credits_earned >= result.total_credits_required;
  const allGroupsMet = result.groups.every(g => g.is_met);
  const nonCourseReqs = version.requirements.non_course_requirements || [];
  const nonCourseMet = nonCourseReqs.length === 0;

  result.eligible = totalMet && externalMet && allGroupsMet && nonCourseMet && tagsMet;

  if (result.eligible) {
    result.summary = `✅ 符合「${program.program_name}」證書資格！`;
  } else {
    const parts = [];
    if (!totalMet) {
      const deficit = result.total_credits_required - result.total_credits_earned;
      parts.push(`總學分不足 ${deficit} 學分（已修 ${result.total_credits_earned}/${result.total_credits_required}）`);
    }
    if (!externalMet) {
      const deficit = result.external_credits_required - result.external_credits_earned;
      parts.push(`外系學分不足 ${deficit} 學分（已修 ${result.external_credits_earned}/${result.external_credits_required}，不含本系${[...ownDepts].join('、')}）`);
    }
    if (!tagsMet) {
      for (const td of tagDetails) {
        if (!td.met) {
          parts.push(`＊號選修不足 ${td.required} 學分（已修 ${td.earned} 學分）`);
        }
      }
    }
    if (!allGroupsMet) {
      const unmet = result.groups.filter(g => !g.is_met).map(g => g.label);
      parts.push(`未滿足：${unmet.join('、')}`);
    }
    result.summary = `❌ 尚未符合「${program.program_name}」證書資格。${parts.join('；')}`;
    result.unmet_requirements = parts;
  }

  return result;
}

// ─── Export for module systems ───────────────────────────────────────────────

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    checkEligibility,
    checkGroup,
    checkSubject,
    resolveCredits,
    isDepartmentValid,
    makeWaiverId,
    getWaiverOptions,
  };
}