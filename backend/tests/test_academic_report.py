from backend.core.academic.report import AcademicReportAPI
from backend.core.storage.integration import AcademicReportStorage


def _course(
    name: str,
    code: str,
    student_id: str,
    *,
    passed: bool = False,
    credit: float = 2.0,
):
    return {
        "courseName": name,
        "courseId": code,
        "studentId": student_id,
        "courseNature": "01",
        "credit": credit,
        "passed": passed,
        "status": "01" if passed else "04",
        "scoreView": "合格" if passed else None,
    }


def test_parse_2025_report_supports_deep_tree_and_new_outside_courses():
    response = {
        "calculatedTime": "2026-07-28 01:20:40",
        "fanbx": {
            "educationalProgramCode": "new-program",
            "name": "2025 示例专业",
            "creditsRequired": 160,
            "creditsEarned": 6,
            "creditsTaken": 4,
            "creditsOutOfProgram": 2,
            "children": [
                {
                    "name": "通识教育课程模块",
                    "creditsRequired": 60,
                    "children": [
                        {
                            "name": "公共基础课",
                            "creditsRequired": 50,
                            "children": [
                                {
                                    "name": "数学与自然科学类",
                                    "creditsRequired": 20,
                                    "children": [
                                        {
                                            "name": "必修",
                                            "courseCategory": "C1",
                                            "id": "node-id",
                                            "courseGroupId": "group-id",
                                            "courseGroupWid": "group-wid",
                                            "creditsRequired": 18,
                                            "passed": True,
                                            "passRequired": False,
                                            "courseCountRequired": 1,
                                            "courseCountTaken": 1,
                                            "checkCourseVOS": [
                                                _course("示例课程", "COURSE-1", "20250001")
                                            ],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        "fawbx": [
            _course("方案外示例课程", "OUTSIDE-1", "20250001", passed=True)
        ],
    }

    report = AcademicReportAPI(None)._parse_report(response)

    assert report.program_code == "new-program"
    assert report.program_name == "2025 示例专业"
    assert report.student_id == "20250001"
    leaf = report.categories[0].children[0].children[0].children[0]
    assert leaf.courses[0].course_name == "示例课程"
    assert leaf.requirement_type == "required"
    assert leaf.is_passed is True
    assert leaf.pass_required is False
    assert report.outside_courses[0].course_name == "方案外示例课程"
    assert report.outside_courses[0].course_category == "方案外课程"
    assert report.outside_courses[0].is_passed == "是"

    serialized = AcademicReportStorage._report_to_dict(None, report)
    serialized_leaf = serialized["categories"][0]["children"][0]["children"][0]["children"][0]
    assert serialized_leaf["wid"] == "group-wid"
    assert serialized_leaf["category_code"] == "C1"
    assert serialized_leaf["requirement_type"] == "required"
    assert serialized_leaf["is_completed"] is True
    assert serialized_leaf["course_count_required"] == 1
    assert serialized_leaf["course_count_taken"] == 1
    assert serialized_leaf["missing_course_count"] == 0


def test_parse_legacy_program_name_and_outside_courses_remains_supported():
    response = {
        "fanbx": {
            "educationalProgramCode": "legacy-program",
            "educationalProgramName": "旧版培养方案",
            "children": [],
            "outsideProgramCourses": [
                {
                    "KCM": "旧版方案外课程",
                    "KCH": "LEGACY-1",
                    "XF": 1,
                    "XSZCJ": "90",
                    "SFJG_DISPLAY": "是",
                }
            ],
        }
    }

    report = AcademicReportAPI(None)._parse_report(response)

    assert report.program_name == "旧版培养方案"
    assert report.outside_courses[0].course_name == "旧版方案外课程"
    assert report.outside_courses[0].course_code == "LEGACY-1"


def test_get_report_separates_fixed_query_context_from_program_code():
    class FakeResponse:
        @staticmethod
        def json():
            return {
                "code": "0",
                "datas": {
                    "queryXyzhbx": {
                        "fanbx": {
                            "educationalProgramCode": "program-2024",
                            "name": "2024 示例方案",
                            "children": [],
                        }
                    }
                },
            }

    class FakeClient:
        username = "20240001"

        def __init__(self):
            self.request_data = None

        def post(self, url, data, headers):
            self.request_data = data
            return FakeResponse()

    client = FakeClient()
    report = AcademicReportAPI(client).get_report(program_code="program-2024")

    assert client.request_data == {
        "fromPage": "grxyjcbg",
        "SCLX": "04",
        "XDLX": "01",
        "PYFADM": "program-2024",
    }
    assert report.student_id == "20240001"
    assert report.grade == "2024"


def test_general_elective_keeps_child_requirements_and_caps_parent_total():
    def leaf(name, required, credit):
        return {
            "name": name,
            "creditsRequired": required,
            "passed": True,
            "checkCourseVOS": [
                _course(name, f"CODE-{name}", "20240001", passed=True, credit=credit)
            ],
        }

    response = {
        "fanbx": {
            "creditsRequired": 13,
            "children": [
                {
                    "name": "通识选修类",
                    "creditsRequired": 13,
                    "passed": True,
                    "children": [
                        {
                            "name": "弹性类别",
                            "creditsRequired": 4,
                            "passed": True,
                            "children": [
                                leaf("推荐组", 0, 2),
                                leaf("普通组", 0, 5),
                            ],
                        },
                        leaf("类别一", 1, 1),
                        leaf("类别二", 2, 2),
                        leaf("类别三", 2, 2),
                        leaf("类别四", 2, 2),
                    ],
                }
            ],
        }
    }

    report = AcademicReportAPI(None)._parse_report(response)
    general_elective = report.categories[0]
    flexible = general_elective.children[0]

    assert flexible.declared_required_credits == 4
    assert flexible.required_credits == 4
    assert flexible.requirement_adjustment == 0
    assert general_elective.requires_child_minimums_and_total is True

    serialized = AcademicReportStorage._report_to_dict(None, report)
    serialized_general = serialized["categories"][0]
    serialized_flexible = serialized_general["children"][0]
    assert serialized_flexible["required_credits"] == 4
    assert serialized_flexible["declared_required_credits"] == 4
    assert serialized_flexible["requirement_adjustment"] == 0
    # 子类实际修读共 14 学分，父类只在汇总后按原始 13 学分封顶。
    assert serialized_general["passed_credits"] == 13
    assert serialized_general["earned_credits"] == 13
    assert serialized_general["remaining_credits"] == 0
    assert serialized_general["aggregate_remaining_credits"] == 0
    assert serialized_general["completion_rate"] == 100
    assert serialized_general["is_completed"] is True


def test_general_elective_requires_total_even_when_every_child_minimum_is_met():
    def leaf(name, required, credit):
        return {
            "name": name,
            "creditsRequired": required,
            "checkCourseVOS": [
                _course(name, f"CODE-{name}", "20240001", passed=True, credit=credit)
            ],
        }

    response = {
        "fanbx": {
            "creditsRequired": 13,
            "children": [
                {
                    "name": "通识选修类",
                    "creditsRequired": 13,
                    "passed": True,
                    "children": [
                        leaf("科学素养", 4, 4),
                        leaf("类别一", 1, 1),
                        leaf("类别二", 2, 2),
                        leaf("类别三", 2, 2),
                        leaf("类别四", 2, 2),
                    ],
                }
            ],
        }
    }

    serialized = AcademicReportStorage._report_to_dict(
        None, AcademicReportAPI(None)._parse_report(response)
    )["categories"][0]

    assert all(child["is_completed"] for child in serialized["children"])
    assert serialized["earned_credits"] == 11
    assert serialized["remaining_credits"] == 2
    assert serialized["aggregate_remaining_credits"] == 2
    # 不采信父节点旧规则给出的 passed=True，必须满足新的总量条件。
    assert serialized["is_completed"] is False


def test_general_elective_requires_every_child_even_when_total_is_met():
    def leaf(name, required, credit):
        return {
            "name": name,
            "creditsRequired": required,
            # 即使远端沿用旧规则称子类已完成，本地仍须检查原始最低学分。
            "passed": True,
            "checkCourseVOS": [
                _course(name, f"CODE-{name}", "20240001", passed=True, credit=credit)
            ],
        }

    response = {
        "fanbx": {
            "creditsRequired": 13,
            "children": [
                {
                    "name": "通识选修类",
                    "creditsRequired": 13,
                    "passed": True,
                    "children": [
                        leaf("科学素养", 4, 3),
                        leaf("类别一", 1, 4),
                        leaf("类别二", 2, 2),
                        leaf("类别三", 2, 2),
                        leaf("类别四", 2, 2),
                    ],
                }
            ],
        }
    }

    serialized = AcademicReportStorage._report_to_dict(
        None, AcademicReportAPI(None)._parse_report(response)
    )["categories"][0]

    assert serialized["earned_credits"] == 13
    assert serialized["remaining_credits"] == 0
    assert serialized["children"][0]["is_completed"] is False
    assert serialized["children"][0]["remaining_credits"] == 1
    assert serialized["aggregate_remaining_credits"] == 0
    assert serialized["is_completed"] is False


def test_general_elective_counts_nested_actual_credits_before_parent_cap():
    response = {
        "fanbx": {
            "creditsRequired": 10,
            "children": [
                {
                    "name": "通识选修类",
                    "creditsRequired": 10,
                    "children": [
                        {
                            "name": "子类甲",
                            "creditsRequired": 4,
                            "children": [
                                {
                                    "name": "内部一",
                                    "creditsRequired": 1,
                                    "checkCourseVOS": [
                                        _course(
                                            "内部超修",
                                            "NESTED-OVERFLOW",
                                            "20240001",
                                            passed=True,
                                            credit=6,
                                        )
                                    ],
                                },
                                {
                                    "name": "内部二",
                                    "creditsRequired": 1,
                                    "checkCourseVOS": [],
                                },
                            ],
                        },
                        {
                            "name": "子类乙",
                            "creditsRequired": 4,
                            "checkCourseVOS": [
                                _course(
                                    "普通课程",
                                    "NORMAL",
                                    "20240001",
                                    passed=True,
                                    credit=4,
                                )
                            ],
                        },
                    ],
                }
            ],
        }
    }

    serialized = AcademicReportStorage._report_to_dict(
        None, AcademicReportAPI(None)._parse_report(response)
    )["categories"][0]

    assert serialized["children"][0]["earned_credits"] == 6
    assert serialized["earned_credits"] == 10
    assert serialized["remaining_credits"] == 0
    assert serialized["is_completed"] is True


def test_general_elective_parent_cap_preserves_passed_before_selected():
    selected_course = _course(
        "已选课程",
        "SELECTED",
        "20240001",
        passed=False,
        credit=2,
    )
    selected_course["status"] = "03"
    response = {
        "fanbx": {
            "creditsRequired": 13,
            "children": [
                {
                    "name": "通识选修类",
                    "creditsRequired": 13,
                    "children": [
                        {
                            "name": "子类甲",
                            "creditsRequired": 5,
                            "checkCourseVOS": [
                                _course("已通过甲", "PASSED-A", "20240001", passed=True, credit=7)
                            ],
                        },
                        {
                            "name": "子类乙",
                            "creditsRequired": 5,
                            "checkCourseVOS": [
                                _course("已通过乙", "PASSED-B", "20240001", passed=True, credit=5),
                                selected_course,
                            ],
                        },
                    ],
                }
            ],
        }
    }

    serialized = AcademicReportStorage._report_to_dict(
        None, AcademicReportAPI(None)._parse_report(response)
    )["categories"][0]

    assert serialized["passed_credits"] == 12
    assert serialized["selected_credits"] == 1
    assert serialized["earned_credits"] == 13
    assert serialized["is_completed"] is True


def test_general_elective_preserves_explicit_child_rule_failure():
    response = {
        "fanbx": {
            "creditsRequired": 10,
            "children": [
                {
                    "name": "通识选修类",
                    "creditsRequired": 10,
                    "children": [
                        {
                            "name": "规则未满足子类",
                            "creditsRequired": 4,
                            "passed": False,
                            "courseCountRequired": 2,
                            "courseCountTaken": 1,
                            "checkCourseVOS": [
                                _course("超额课程", "RULE-FAIL", "20240001", passed=True, credit=6)
                            ],
                        },
                        {
                            "name": "已完成子类",
                            "creditsRequired": 4,
                            "passed": True,
                            "checkCourseVOS": [
                                _course("普通课程", "RULE-PASS", "20240001", passed=True, credit=4)
                            ],
                        },
                    ],
                }
            ],
        }
    }

    serialized = AcademicReportStorage._report_to_dict(
        None, AcademicReportAPI(None)._parse_report(response)
    )["categories"][0]

    assert serialized["earned_credits"] == 10
    assert serialized["children"][0]["remaining_credits"] == 0
    assert serialized["children"][0]["is_completed"] is False
    assert serialized["is_completed"] is False


def test_general_elective_is_not_adjusted_when_children_already_cover_parent():
    response = {
        "fanbx": {
            "children": [
                {
                    "name": "通识选修类",
                    "creditsRequired": 10,
                    "children": [
                        {
                            "name": "弹性类别",
                            "creditsRequired": 3,
                            "children": [
                                {
                                    "name": "内部组",
                                    "creditsRequired": 0,
                                    "checkCourseVOS": [],
                                }
                            ],
                        },
                        {"name": "类别一", "creditsRequired": 1},
                        {"name": "类别二", "creditsRequired": 2},
                        {"name": "类别三", "creditsRequired": 2},
                        {"name": "类别四", "creditsRequired": 2},
                    ],
                }
            ]
        }
    }

    report = AcademicReportAPI(None)._parse_report(response)
    flexible = report.categories[0].children[0]

    assert flexible.declared_required_credits == 3
    assert flexible.required_credits == 3
    assert flexible.requirement_adjustment == 0
