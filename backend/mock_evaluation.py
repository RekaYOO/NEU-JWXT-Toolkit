"""
评教测试模式 Mock 数据层
测试模式下不连接真实 zljk.neu.edu.cn，所有评教接口返回本地模拟数据
"""

from typing import Dict, Any, List, Optional

# ============================================================
# 测试模式开关：发布前设为 False
# ============================================================
EVAL_TEST_MODE = 0

# 内存状态：记录已评价课程 {xspjid: {is_evaluated, avg_score, indicators}}
_test_eval_states: Dict[str, Any] = {}

SCORE_MAP = {6: 100, 5: 90, 4: 80, 3: 70, 2: 60, 1: 50}

LEVEL_JSON = [
    {"mc": "优秀", "df": 100, "value": 6},
    {"mc": "很好", "df": 90, "value": 5},
    {"mc": "好", "df": 80, "value": 4},
    {"mc": "较好", "df": 70, "value": 3},
    {"mc": "一般", "df": 60, "value": 2},
    {"mc": "较差", "df": 50, "value": 1},
]

MOCK_INDICATORS = [
    {"zbid": "zb-001", "zbmc": "教学目标明确", "evaltype": 1, "sfdx": 0, "sfbt": 1, "weight": 10, "fz": 100, "level_json": LEVEL_JSON, "jsjx": "教师能够清晰阐述课程目标", "sort": 1, "parent_id": "root"},
    {"zbid": "zb-002", "zbmc": "教学内容充实", "evaltype": 1, "sfdx": 0, "sfbt": 1, "weight": 10, "fz": 100, "level_json": LEVEL_JSON, "jsjx": "内容丰富，重点突出", "sort": 2, "parent_id": "root"},
    {"zbid": "zb-003", "zbmc": "教学方法得当", "evaltype": 1, "sfdx": 0, "sfbt": 1, "weight": 10, "fz": 100, "level_json": LEVEL_JSON, "jsjx": "方法灵活，注重启发", "sort": 3, "parent_id": "root"},
    {"zbid": "zb-004", "zbmc": "课堂气氛活跃", "evaltype": 1, "sfdx": 0, "sfbt": 1, "weight": 10, "fz": 100, "level_json": LEVEL_JSON, "jsjx": "师生互动积极", "sort": 4, "parent_id": "root"},
    {"zbid": "zb-005", "zbmc": "师生互动良好", "evaltype": 1, "sfdx": 0, "sfbt": 1, "weight": 10, "fz": 100, "level_json": LEVEL_JSON, "jsjx": "尊重学生，耐心解答", "sort": 5, "parent_id": "root"},
    {"zbid": "zb-006", "zbmc": "教学态度认真", "evaltype": 1, "sfdx": 0, "sfbt": 1, "weight": 10, "fz": 100, "level_json": LEVEL_JSON, "jsjx": "备课充分，精神饱满", "sort": 6, "parent_id": "root"},
    {"zbid": "zb-007", "zbmc": "板书课件清晰", "evaltype": 1, "sfdx": 0, "sfbt": 1, "weight": 10, "fz": 100, "level_json": LEVEL_JSON, "jsjx": "条理清楚，便于记录", "sort": 7, "parent_id": "root"},
    {"zbid": "zb-008", "zbmc": "作业批改及时", "evaltype": 1, "sfdx": 0, "sfbt": 1, "weight": 10, "fz": 100, "level_json": LEVEL_JSON, "jsjx": "反馈及时，针对性强", "sort": 8, "parent_id": "root"},
    {"zbid": "zb-009", "zbmc": "教学手段多样", "evaltype": 1, "sfdx": 1, "sfbt": 0, "weight": 10, "fz": 100, "level_json": LEVEL_JSON, "jsjx": "善用多媒体与案例教学", "sort": 9, "parent_id": "root"},
    {"zbid": "zb-010", "zbmc": "对课程的总体评价与建议", "evaltype": 2, "sfdx": 0, "sfbt": 0, "weight": 0, "fz": 0, "level_json": [], "jsjx": "请填写您对课程的总体评价", "sort": 10, "parent_id": "root"},
    {"zbid": "zb-011", "zbmc": "其他意见（选填）", "evaltype": 2, "sfdx": 0, "sfbt": 0, "weight": 0, "fz": 0, "level_json": [], "jsjx": "如有其他意见请填写", "sort": 11, "parent_id": "root"},
]

MOCK_TASK = {
    "task_id": "test-task-2025-spring",
    "task_name": "2025-2026学年春季学期学生评教（测试模式）",
    "total_count": 55,
    "pending_count": 50,
    "evaluated_count": 5,
    "status": 1,
}


def _generate_mock_courses():
    depts = ['计算机学院', '软件学院', '信息学院', '机械学院', '管理学院']
    types = ['必修', '选修']
    courses = []
    
    for i in range(50):
        char = chr(65 + (i % 26))
        suffix = str(i // 26) if i >= 26 else ''
        courses.append({
            "xspjid": f"test-xspjid-{i}",
            "task_id": "test-task-2025-spring",
            "task_name": "2025-2026学年春季学期学生评教（测试模式）",
            "course_name": f"测试课程 {i + 1}",
            "teacher_name": f"教师 {char}{suffix}",
            "teacher_code": f"t{i:03d}",
            "department": depts[i % len(depts)],
            "department_id": f"dept-{(i % len(depts)):02d}",
            "course_type_name": types[i % len(types)],
            "is_submit": "1",
            "is_evaluated": False,
            "is_kpj": "1",
            "score": None,
            "avg_score": None,
        })
    
    for i in range(50, 55):
        score = 82 + ((i - 50) * 3)
        courses.append({
            "xspjid": f"test-xspjid-{i}",
            "task_id": "test-task-2025-spring",
            "task_name": "2025-2026学年春季学期学生评教（测试模式）",
            "course_name": f"测试课程 {i + 1}",
            "teacher_name": f"教师 已评{i - 49}",
            "teacher_code": f"t{i:03d}",
            "department": depts[i % len(depts)],
            "department_id": f"dept-{(i % len(depts)):02d}",
            "course_type_name": types[i % len(types)],
            "is_submit": "0",
            "is_evaluated": True,
            "is_kpj": "1",
            "score": score,
            "avg_score": score,
        })
    
    return courses


MOCK_COURSES = _generate_mock_courses()


# ── 内部辅助函数 ──────────────────────────────────────────────────────────

def _calculate_scored_indicators(strategy, custom_scores, text_results):
    """根据策略计算每题得分"""
    indicators = [dict(ind) for ind in MOCK_INDICATORS]
    
    if text_results:
        for ind in indicators:
            if ind["zbid"] in text_results:
                ind["result"] = text_results[ind["zbid"]]
    
    selection_count = 0
    for ind in indicators:
        if ind["evaltype"] != 1:
            continue
        
        if strategy == "custom" and custom_scores and ind["zbid"] in custom_scores:
            ind["dfdj"] = custom_scores[ind["zbid"]]
        elif strategy == "highest":
            if ind["sfdx"] == 1:
                ind["dfdj"] = [6, 5]
            else:
                ind["dfdj"] = 5 if selection_count == 0 else 6
        elif strategy == "lowest":
            if ind["sfdx"] == 1:
                ind["dfdj"] = [1, 2] if selection_count == 0 else [1]
            else:
                ind["dfdj"] = 2 if selection_count == 0 else 1
        selection_count += 1
    
    return indicators


def _validate_scoring(indicators):
    """验证评分"""
    errors = []
    selection_scores = []
    
    for ind in indicators:
        if ind["evaltype"] == 1:
            if ind["sfbt"] == 1 and ind.get("dfdj") is None:
                errors.append(f"必填指标未评分: {ind['zbmc']}")
            if ind.get("dfdj") is not None:
                if isinstance(ind["dfdj"], list):
                    selection_scores.extend(ind["dfdj"])
                else:
                    selection_scores.append(ind["dfdj"])
        elif ind["sfbt"] == 1 and not ind.get("result"):
            errors.append(f"必填文本指标未填写: {ind['zbmc']}")
    
    if len(selection_scores) > 1 and len(set(str(s) for s in selection_scores)) == 1:
        errors.append("评价选项不能全部相同")
    
    return {"valid": len(errors) == 0, "errors": errors}


def _calculate_scores(indicators):
    """计算每题分数和均分"""
    total_score = 0
    score_count = 0
    
    for ind in indicators:
        if ind["evaltype"] == 1 and ind.get("dfdj") is not None:
            if isinstance(ind["dfdj"], list):
                ind["score"] = [SCORE_MAP.get(d, 0) for d in ind["dfdj"]]
                total_score += max(ind["score"])
            else:
                ind["score"] = SCORE_MAP.get(ind["dfdj"], 0)
                total_score += ind["score"]
            score_count += 1
    
    avg_score = round(total_score / score_count, 1) if score_count > 0 else 0
    return indicators, avg_score, score_count


# ── Mock API 实现 ──────────────────────────────────────────────────────────

def get_mock_tasks():
    # 动态计算已评/待评数量
    evaluated = sum(1 for c in MOCK_COURSES if c["is_evaluated"] or c["xspjid"] in _test_eval_states)
    total = len(MOCK_COURSES)
    task = dict(MOCK_TASK)
    task["evaluated_count"] = evaluated
    task["pending_count"] = total - evaluated
    task["total_count"] = total
    return {"tasks": [task], "total": 1, "xnxq": "2025-2026-2"}


def get_mock_courses(task_id):
    courses = [dict(c) for c in MOCK_COURSES]
    
    # 应用已评状态覆盖
    for c in courses:
        state = _test_eval_states.get(c["xspjid"])
        if state:
            c["is_evaluated"] = state["is_evaluated"]
            c["is_submit"] = "0" if state["is_evaluated"] else "1"
            c["avg_score"] = state["avg_score"]
            c["score"] = state["avg_score"]
    
    pending = [c for c in courses if not c["is_evaluated"]]
    completed = [c for c in courses if c["is_evaluated"]]
    
    return {
        "task_id": task_id,
        "courses": courses,
        "total": len(courses),
        "pending": len(pending),
        "completed": len(completed),
    }


def get_mock_indicators(xspjid, task_id):
    indicators = [dict(ind) for ind in MOCK_INDICATORS]
    state = _test_eval_states.get(xspjid)
    
    if state and state.get("indicators"):
        for ind in indicators:
            saved = next((s for s in state["indicators"] if s["zbid"] == ind["zbid"]), None)
            if saved:
                ind["dfdj"] = saved.get("dfdj")
                ind["score"] = saved.get("score")
                ind["result"] = saved.get("result")
    
    scored_indicators = []
    total_eval_score = 0
    scored_count = 0
    for ind in indicators:
        item = dict(ind)
        if item.get("dfdj") is not None:
            if isinstance(item["dfdj"], list):
                item["score"] = [SCORE_MAP.get(d, 0) for d in item["dfdj"]]
                total_eval_score += max(item["score"])
            else:
                item["score"] = SCORE_MAP.get(item["dfdj"], 0)
                total_eval_score += item["score"]
            scored_count += 1
        else:
            item["score"] = None
        scored_indicators.append(item)
    
    avg_score = round(total_eval_score / scored_count, 1) if scored_count > 0 else None
    
    return {
        "libid": "lib-test-001",
        "libname": "本科生评教指标（测试）",
        "preface": "请根据您的真实感受对课程进行评价。评价选项不能全部相同，否则系统会拦截。",
        "total_score": 1000,
        "indicators": scored_indicators,
        "total_indicators": len(indicators),
        "selection_count": len([i for i in indicators if i["evaltype"] == 1]),
        "text_count": len([i for i in indicators if i["evaltype"] != 1]),
        "avg_score": avg_score,
        "scored_count": scored_count,
    }


def mock_submit(request):
    """模拟单门课程提交"""
    course = next((c for c in MOCK_COURSES if c["xspjid"] == request.xspjid), None)
    if not course:
        return {"success": False, "message": f"未找到课程: xspjid={request.xspjid}"}
    
    scored = _calculate_scored_indicators(request.strategy, request.custom_scores, request.text_results)
    validation = _validate_scoring(scored)
    
    if not validation["valid"]:
        return {"success": False, "message": "评分验证失败", "errors": validation["errors"]}
    
    indicators, avg_score, _ = _calculate_scores(scored)
    
    # 实际提交：更新内存状态
    _test_eval_states[request.xspjid] = {
        "is_evaluated": True,
        "avg_score": avg_score,
        "indicators": [
            {
                "zbid": ind["zbid"],
                "dfdj": ind.get("dfdj"),
                "score": ind.get("score"),
                "result": ind.get("result"),
            }
            for ind in indicators
        ],
    }
    
    return {
        "success": True,
        "message": "评教提交成功",
        "data": {"task": {"zpf": avg_score}},
        "course_name": course["course_name"],
        "teacher_name": course["teacher_name"],
    }


def mock_batch(request):
    """模拟批量提交"""
    courses = [c for c in MOCK_COURSES if not c["is_evaluated"]]
    if request.xspjids:
        courses = [c for c in courses if c["xspjid"] in request.xspjids]
    
    if not courses:
        return {
            "results": [],
            "total": 0,
            "pending_count": 0,
            "success_count": 0,
            "message": "没有待评课程",
        }
    
    results = []
    for course in courses:
        scored = _calculate_scored_indicators(request.strategy, request.custom_scores, None)
        validation = _validate_scoring(scored)
        indicators, avg_score, _ = _calculate_scores(scored)
        
        _test_eval_states[course["xspjid"]] = {
            "is_evaluated": True,
            "avg_score": avg_score,
            "indicators": [
                {
                    "zbid": ind["zbid"],
                    "dfdj": ind.get("dfdj"),
                    "score": ind.get("score"),
                    "result": ind.get("result"),
                }
                for ind in indicators
            ],
        }
        results.append({
            "course_name": course["course_name"],
            "teacher_name": course["teacher_name"],
            "success": True,
            "avg_score": avg_score,
        })
    
    success_count = sum(1 for r in results if r.get("success"))
    return {
        "results": results,
        "total": len(results),
        "pending_count": len(courses),
        "success_count": success_count,
        "message": "批量评教完成",
    }
