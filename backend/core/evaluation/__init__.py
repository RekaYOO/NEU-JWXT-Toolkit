"""
neu_evaluation
==============

东北大学教学质量评价系统 API

功能：
- 获取学生评教任务列表
- 获取评教详情（指标项、打分项）
- 自动评分策略（最高分/最低分/自定义）
- 提交评教结果

注意：评教系统不支持重试，提交前务必确认数据正确。
"""

from backend.core.evaluation.api import EvaluationAPI

__all__ = ["EvaluationAPI"]
