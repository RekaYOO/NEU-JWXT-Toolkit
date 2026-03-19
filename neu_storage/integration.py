"""
neu_storage/integration.py
==========================
与 neu_auth 和 neu_academic 的集成

提供智能合并本地和远程成绩的功能
"""

import os
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import sys
sys.path.insert(0, r"E:\code\NEUT")

from neu_auth import NEUAuthClient
from neu_academic.api import CourseScore
from .storage import Storage, StorageConfig


# 本地数据过期时间（小时）- 3天
CACHE_EXPIRY_HOURS = 72


class AcademicStorage:
    """
    成绩存储助手 - 智能合并本地和远程数据
    """
    
    def __init__(self, storage: Optional[Storage] = None):
        """
        初始化
        
        Args:
            storage: Storage 实例，None则创建默认
        """
        self.storage = storage or Storage()
    
    def get_scores_smart(self, auth: NEUAuthClient, 
                         force_refresh: bool = False) -> Dict:
        """
        智能获取成绩
        
        策略：
        1. 如果 force_refresh=True，直接获取远程数据
        2. 如果本地没有数据，获取远程数据
        3. 如果本地数据过期（>24小时），获取远程数据
        4. 否则返回本地数据
        
        Args:
            auth: NEUAuthClient 实例
            force_refresh: 强制刷新
            
        Returns:
            {
                "scores": List[CourseScore],
                "source": "local" | "remote",
                "last_update": datetime,
                "is_fresh": bool
            }
        """
        # 检查是否需要刷新
        need_refresh = force_refresh
        last_update = None
        
        if not need_refresh:
            last_update = self.storage.get_last_update_time()
            if last_update is None:
                need_refresh = True
            else:
                # 检查是否过期
                age = datetime.now() - last_update
                if age > timedelta(hours=CACHE_EXPIRY_HOURS):
                    need_refresh = True
        
        # 获取远程数据
        if need_refresh:
            try:
                scores = auth.academic.get_scores()
                overall_gpa = auth.academic.get_overall_gpa()
                
                # 保存到本地
                meta = {
                    "fetch_time": datetime.now().isoformat(),
                    "username": auth.username,
                    "total_courses": len(scores),
                    "overall_gpa": overall_gpa,
                    "source": "remote"
                }
                self.storage.save_scores(scores, metadata=meta)
                
                return {
                    "scores": scores,
                    "source": "remote",
                    "last_update": datetime.now(),
                    "is_fresh": True,
                    "overall_gpa": overall_gpa
                }
            except Exception as e:
                # 远程获取失败，尝试使用本地数据
                local_data = self.storage.load_scores_with_meta()
                if local_data["scores"]:
                    return {
                        "scores": local_data["scores"],
                        "source": "local",
                        "last_update": self.storage.get_last_update_time(),
                        "is_fresh": False,
                        "error": str(e),
                        "overall_gpa": local_data["meta"].get("overall_gpa")
                    }
                raise
        
        # 使用本地数据
        local_data = self.storage.load_scores_with_meta()
        return {
            "scores": local_data["scores"],
            "source": "local",
            "last_update": last_update or self.storage.get_last_update_time(),
            "is_fresh": False,
            "overall_gpa": local_data["meta"].get("overall_gpa"),
            "meta": local_data["meta"]
        }
    
    def refresh_scores(self, auth: NEUAuthClient) -> Dict:
        """
        强制刷新成绩数据
        
        Args:
            auth: NEUAuthClient 实例
            
        Returns:
            刷新结果
        """
        try:
            scores = auth.academic.get_scores()
            overall_gpa = auth.academic.get_overall_gpa()
            
            # 保存
            meta = {
                "fetch_time": datetime.now().isoformat(),
                "username": auth.username,
                "total_courses": len(scores),
                "overall_gpa": overall_gpa,
                "source": "remote"
            }
            filepath = self.storage.save_scores(scores, metadata=meta)
            
            return {
                "success": True,
                "message": f"已刷新 {len(scores)} 门课程",
                "total_courses": len(scores),
                "overall_gpa": overall_gpa,
                "filepath": filepath
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"刷新失败: {str(e)}"
            }
    
    def compare_with_remote(self, auth: NEUAuthClient) -> Dict:
        """
        比较本地和远程成绩差异
        """
        local_data = self.storage.load_scores_with_meta()
        local_scores = local_data["scores"]
        remote_scores = auth.academic.get_scores()
        
        # 按课程代码建立索引
        local_dict = {s.code: s for s in local_scores}
        remote_dict = {s.code: s for s in remote_scores}
        
        local_codes = set(local_dict.keys())
        remote_codes = set(remote_dict.keys())
        
        new_courses = remote_codes - local_codes
        removed_courses = local_codes - remote_codes
        
        changed = []
        for code in local_codes & remote_codes:
            l, r = local_dict[code], remote_dict[code]
            if l.score != r.score or l.gpa != r.gpa:
                changed.append({
                    "code": code,
                    "name": r.name,
                    "local_score": l.score,
                    "remote_score": r.score,
                    "local_gpa": l.gpa,
                    "remote_gpa": r.gpa
                })
        
        return {
            "local_count": len(local_scores),
            "remote_count": len(remote_scores),
            "new_courses": list(new_courses),
            "removed_courses": list(removed_courses),
            "changed_scores": changed
        }


class AutoLoginManager:
    """
    自动登录管理器
    
    支持两种自动登录方式：
    1. Cookie 恢复（免密，优先）
    2. 密码重新登录（兜底）
    """
    
    def __init__(self, storage: Optional[Storage] = None, cookie_file: Optional[str] = None):
        self.storage = storage or Storage()
        self.cookie_file = cookie_file
    
    def try_auto_login(self) -> Optional[NEUAuthClient]:
        """
        尝试自动登录
        
        恢复优先级：
        1. 用 Cookie 刷新票据（免密）
        2. 用密码重新登录
        
        Returns:
            NEUAuthClient 或 None
        """
        creds = self.storage.load_credentials()
        if not creds:
            return None
        
        username, password = creds
        try:
            # 创建客户端时会自动加载 Cookie
            client = NEUAuthClient(
                username, 
                password,
                cookie_file=self.cookie_file
            )
            # ensure_login 内部会优先用 Cookie 刷新
            if client.ensure_login():
                return client
        except Exception:
            pass
        
        return None
    
    def save_login(self, auth: NEUAuthClient) -> None:
        """保存登录信息"""
        self.storage.save_credentials(auth.username, auth.password)
    
    def clear_login(self) -> None:
        """清除登录信息"""
        self.storage.clear_credentials()


class AcademicReportStorage:
    """
    培养计划存储助手
    """
    
    REPORT_FILENAME = "academic_report.json"
    
    def __init__(self, storage: Optional[Storage] = None):
        self.storage = storage or Storage()
    
    def save_report(self, report_data: Dict[str, Any], username: str) -> str:
        """
        保存培养计划
        
        Args:
            report_data: 培养计划数据
            username: 用户名
            
        Returns:
            保存的文件路径
        """
        data = {
            "report": report_data,
            "username": username,
            "saved_at": datetime.now().isoformat(),
        }
        return self.storage.save_json(data, self.REPORT_FILENAME)
    
    def load_report(self) -> Optional[Dict[str, Any]]:
        """
        加载培养计划
        
        Returns:
            培养计划数据，不存在返回 None
        """
        data = self.storage.load_json(self.REPORT_FILENAME)
        if data:
            return {
                "report": data.get("report"),
                "username": data.get("username"),
                "saved_at": data.get("saved_at"),
            }
        return None
    
    def get_last_update_time(self) -> Optional[datetime]:
        """获取最后更新时间"""
        filepath = os.path.join(self.storage.config.data_dir, self.REPORT_FILENAME)
        if not os.path.exists(filepath):
            return None
        mtime = os.path.getmtime(filepath)
        return datetime.fromtimestamp(mtime)
    
    def get_scores_update_time(self) -> Optional[datetime]:
        """获取成绩文件最后更新时间"""
        scores_path = os.path.join(self.storage.config.data_dir, 
                                   self.storage.config.scores_filename)
        if not os.path.exists(scores_path):
            return None
        mtime = os.path.getmtime(scores_path)
        return datetime.fromtimestamp(mtime)
    
    def get_report_smart(self, auth: NEUAuthClient, force_refresh: bool = False) -> Dict:
        """
        智能获取培养计划
        
        策略：
        1. 如果 force_refresh=True，直接获取远程数据
        2. 如果本地没有数据，获取远程数据
        3. 如果成绩更新时间 > 培养计划更新时间，获取远程数据（成绩更新了则培养计划也更新）
        4. 否则返回本地数据
        
        Args:
            auth: NEUAuthClient 实例
            force_refresh: 强制刷新
            
        Returns:
            {
                "report": Dict,
                "source": "local" | "remote",
                "last_update": datetime,
                "is_fresh": bool
            }
        """
        need_refresh = force_refresh
        last_update = None
        
        if not need_refresh:
            last_update = self.get_last_update_time()
            if last_update is None:
                # 本地没有数据，需要刷新
                need_refresh = True
            else:
                # 检查成绩是否比培养计划更新
                scores_update_time = self.get_scores_update_time()
                if scores_update_time and scores_update_time > last_update:
                    need_refresh = True
        
        # 获取远程数据
        if need_refresh:
            try:
                from neu_academic.report import AcademicReportAPI
                api = AcademicReportAPI(auth)
                report = api.get_report()
                
                if report is None:
                    raise Exception("获取培养计划失败")
                
                # 转换为字典
                report_dict = self._report_to_dict(report)
                
                # 保存到本地
                self.save_report(report_dict, auth.username)
                
                return {
                    "report": report_dict,
                    "source": "remote",
                    "last_update": datetime.now(),
                    "is_fresh": True
                }
            except Exception as e:
                # 远程获取失败，尝试使用本地数据
                local_data = self.load_report()
                if local_data and local_data.get("report"):
                    return {
                        "report": local_data["report"],
                        "source": "local",
                        "last_update": self.get_last_update_time(),
                        "is_fresh": False,
                        "error": str(e)
                    }
                raise
        
        # 使用本地数据
        local_data = self.load_report()
        if local_data and local_data.get("report"):
            return {
                "report": local_data["report"],
                "source": "local",
                "last_update": last_update or self.get_last_update_time(),
                "is_fresh": False
            }
        
        # 本地没有数据，强制获取远程
        return self.get_report_smart(auth, force_refresh=True)
    
    def refresh_report(self, auth: NEUAuthClient) -> Dict:
        """
        强制刷新培养计划
        
        Args:
            auth: NEUAuthClient 实例
            
        Returns:
            刷新结果
        """
        try:
            from neu_academic.report import AcademicReportAPI
            api = AcademicReportAPI(auth)
            report = api.get_report()
            
            if report is None:
                return {
                    "success": False,
                    "message": "获取培养计划失败"
                }
            
            report_dict = self._report_to_dict(report)
            filepath = self.save_report(report_dict, auth.username)
            
            return {
                "success": True,
                "message": f"已刷新培养计划",
                "filepath": filepath
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"刷新失败: {str(e)}"
            }
    
    def _report_to_dict(self, report) -> Dict[str, Any]:
        """将 AcademicReport 转换为字典，包含完整的类别层级结构"""
        import uuid
        
        def is_course_passed(course) -> bool:
            """判断课程是否已通过"""
            if isinstance(course.is_passed, str):
                return course.is_passed == "是"
            return bool(course.is_passed)
        
        def is_course_selected(course) -> bool:
            """判断课程是否已选课但未通过"""
            return not is_course_passed(course) and course.status == "已选课"
        
        def is_course_planned(course) -> bool:
            """判断课程是否未修读"""
            return not is_course_passed(course) and not is_course_selected(course)
        
        def get_course_status_display(course) -> str:
            """获取课程状态显示文本"""
            if is_course_passed(course):
                return "已通过"
            if is_course_selected(course):
                return "已选课"
            return "未修读"
        
        def category_to_dict(cat, path="", depth=0):
            """递归转换类别节点为字典"""
            current_path = f"{path} > {cat.name}" if path else cat.name
            path_array = current_path.split(" > ") if current_path else [cat.name]
            
            # 递归处理子节点（先处理子节点，因为父节点需要汇总）
            children_dicts = []
            children_passed = 0
            children_selected = 0
            children_planned = 0
            
            # 汇总子类别的学分（父层级汇总时，每个子类别的已修学分不能超过其要求学分）
            children_required = 0
            children_earned_limited = 0  # 受限的已修学分（用于父层级计算）
            
            for child in cat.children:
                child_dict = category_to_dict(child, current_path, depth + 1)
                children_dicts.append(child_dict)
                children_required += child_dict['required_credits']
                
                # 父层级汇总时，子类别的已修学分不能超过其要求学分（防止超额溢出）
                child_earned = child_dict['earned_credits']
                child_required = child_dict['required_credits']
                
                # 判断子节点是否是"实际类别"（有子节点但required>0，如科学素养类）
                child_is_actual_category = child_dict.get('has_children', False) and child_required > 0
                
                if child_required > 0 and child_earned > child_required:
                    # 超额情况下，限制为要求学分
                    # 对于实际类别，使用要求学分；对于叶节点，按比例限制
                    if child_is_actual_category:
                        # 实际类别：直接限制为要求学分
                        # passed和selected按比例分配
                        ratio = child_required / child_earned
                        children_passed += child_dict['passed_credits'] * ratio
                        children_selected += child_dict['selected_credits'] * ratio
                    else:
                        # 叶节点超额：按比例限制
                        ratio = child_required / child_earned
                        children_passed += child_dict['passed_credits'] * ratio
                        children_selected += child_dict['selected_credits'] * ratio
                else:
                    # 未超额，直接使用
                    children_passed += child_dict['passed_credits']
                    children_selected += child_dict['selected_credits']
                
                children_planned += child_dict['planned_credits']
            
            # 计算本节点的课程学分统计
            node_passed_credits = 0
            node_selected_credits = 0
            node_planned_credits = 0
            
            for c in cat.courses:
                if is_course_passed(c):
                    node_passed_credits += c.credit
                elif is_course_selected(c):
                    node_selected_credits += c.credit
                else:
                    node_planned_credits += c.credit
            
            # 总学分统计
            # 自己计算每个类别的学分，而不是依赖接口返回的值
            # 注意：父层级使用受限的children学分，防止超额溢出
            total_passed = node_passed_credits + children_passed
            total_selected = node_selected_credits + children_selected
            total_planned = node_planned_credits + children_planned
            total_earned = total_passed + total_selected  # 已获得 = 已通过 + 已选
            
            # 要求学分使用教务系统返回的原始值（即使教务系统有BUG也以其为准）
            total_required = cat.required_credits
            
            # 判断是否是叶节点（无子节点）
            # 注意：有子节点但自身有要求学分的节点（如科学素养类）被视为实际类别，不是虚拟父节点
            # 这类节点显示实际学分，但父节点汇总时只计算限制后的学分
            is_leaf = len(cat.children) == 0
            
            # 只有"虚拟父节点"（required=0 但有子节点）才需要限制
            # "实际类别"（required>0 且有子节点，如科学素养类）显示实际学分，不限制
            # 真正的叶节点（无子节点）也不限制
            is_virtual_parent = (not is_leaf) and (total_required == 0)
            
            if is_virtual_parent and total_earned > 0:
                # 虚拟父节点：限制为0（实际学分由子节点显示）
                total_passed = 0
                total_selected = 0
                total_earned = 0
            # 实际类别（有子节点且required>0）不限制，显示实际学分
            
            # 生成唯一ID（使用稳定的ID，基于路径）
            import hashlib
            wid = hashlib.md5(current_path.encode()).hexdigest()[:8]
            
            return {
                "wid": wid,
                "name": cat.name,
                "category_code": "",
                "depth": depth,
                "path": current_path,
                "path_array": path_array,
                "is_leaf": is_leaf,
                "has_children": len(cat.children) > 0,
                "required_credits": round(total_required, 2),
                # 学分统计
                "passed_credits": round(total_passed, 2),
                "selected_credits": round(total_selected, 2),
                "planned_credits": round(total_planned, 2),
                "earned_credits": round(total_earned, 2),
                "remaining_credits": round(max(0, total_required - total_earned), 2),
                # 完成度
                "completion_rate": round(total_earned / cat.required_credits * 100, 2) if cat.required_credits > 0 else 100,
                "is_completed": total_earned >= cat.required_credits,
                # 课程列表 - 包含完整类别路径
                "courses": [
                    {
                        "course_name": c.course_name,
                        "course_code": c.course_code,
                        "course_nature": c.course_nature,
                        "credit": c.credit,
                        "score": c.score,
                        "is_passed": is_course_passed(c),
                        "is_selected": is_course_selected(c),
                        "is_planned": is_course_planned(c),
                        "status": c.status,
                        "status_display": get_course_status_display(c),
                        "term_code": c.score_term or c.select_term or c.plan_term,
                        "select_term_code": c.select_term,
                        "exam_type": c.exam_type,
                        "is_core": False,
                        "substitute_course_name": c.substitute_course,
                        "substitute_credit": float(c.substitute_credit) if c.substitute_credit else 0,
                        "dept_code": c.department,
                        "category_path": path_array,  # 完整路径数组
                        "category_name": cat.name,  # 直接类别名称
                    }
                    for c in cat.courses
                ],
                # 子类别
                "children": children_dicts,
            }
        
        # 转换所有顶层类别
        categories = [category_to_dict(cat) for cat in report.categories]
        
        # 计算总学分统计（从各个类别累加）
        total_required = report.total_required
        total_passed = sum(cat['passed_credits'] for cat in categories)
        total_selected = sum(cat['selected_credits'] for cat in categories)
        total_earned = total_passed + total_selected
        
        return {
            "student_name": report.student_name,
            "student_id": report.student_id,
            "grade": getattr(report, 'grade', ''),
            "college": getattr(report, 'college', ''),
            "major": getattr(report, 'major', ''),
            "class_name": getattr(report, 'class_name', ''),
            "expected_graduation": getattr(report, 'expected_graduation', ''),
            "program_code": report.program_code,
            "program_name": getattr(report, 'program_name', ''),
            "calculated_time": report.calculated_time,
            # 总学分统计
            "credit_summary": {
                "total_required": total_required,
                "total_passed": round(total_passed, 2),
                "total_selected": round(total_selected, 2),
                "total_earned": round(total_earned, 2),
                "total_remaining": round(max(0, total_required - total_earned), 2),
                "completion_rate": round(total_earned / total_required * 100, 2) if total_required > 0 else 0,
            },
            # 类别层级结构
            "categories": categories,
            # 方案外课程
            "outside_courses": [
                {
                    "course_name": c.course_name,
                    "course_code": c.course_code,
                    "course_nature": c.course_nature,
                    "credit": c.credit,
                    "score": c.score,
                    "is_passed": is_course_passed(c),
                    "is_selected": is_course_selected(c),
                    "is_planned": is_course_planned(c),
                    "status": c.status,
                    "status_display": get_course_status_display(c),
                    "term_code": c.score_term or c.select_term,
                    "select_term_code": c.select_term,
                }
                for c in report.outside_courses
            ]
        }


def quick_save(auth: NEUAuthClient, data_dir: Optional[str] = None) -> Dict:
    """
    一键保存所有数据
    """
    config = StorageConfig(data_dir=data_dir) if data_dir else None
    storage = Storage(config)
    academic_storage = AcademicStorage(storage)
    
    result = academic_storage.refresh_scores(auth)
    
    return {
        "scores": result,
        "storage_info": storage.get_storage_info()
    }
