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


def test_general_elective_residual_is_assigned_to_single_flexible_child():
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
    assert flexible.required_credits == 6
    assert flexible.requirement_adjustment == 2

    serialized = AcademicReportStorage._report_to_dict(None, report)
    serialized_general = serialized["categories"][0]
    serialized_flexible = serialized_general["children"][0]
    assert serialized_flexible["required_credits"] == 6
    assert serialized_flexible["declared_required_credits"] == 4
    assert serialized_flexible["requirement_adjustment"] == 2
    assert serialized_general["passed_credits"] == 13
    assert serialized_general["remaining_credits"] == 0


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
