import os
import json
from typing import List
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends

from backend.app.dependencies import _storage, _api_logger, get_gpa_simulation_dir
from backend.app.schemas import GPASimulationExportRequest, GPASimulationFile
from backend.core.auth import NEUAuthClient
from backend.app.dependencies import require_auth

router = APIRouter()


@router.post("/gpa-simulation/export")
async def export_gpa_simulation(
    request: GPASimulationExportRequest,
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    导出GPA模拟数据到data目录

    保存到 data/gpa_simulations/ 目录下
    """
    try:
        # 确保文件名安全
        safe_filename = os.path.basename(request.filename)
        if not safe_filename.endswith('.json'):
            safe_filename += '.json'

        filepath = os.path.join(get_gpa_simulation_dir(), safe_filename)

        # 添加导出元数据
        export_data = {
            **request.data,
            "export_info": {
                "exported_by": auth.username,
                "exported_at": datetime.now().isoformat(),
                "version": "1.0"
            }
        }

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)

        _api_logger.info(f"[GPA-Sim] 导出成功: {safe_filename}, user={auth.username}")
        return {
            "success": True,
            "filename": safe_filename,
            "path": filepath
        }
    except Exception as e:
        _api_logger.error(f"[GPA-Sim] 导出失败: {e}")
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}")


@router.get("/gpa-simulation/files", response_model=List[GPASimulationFile])
async def list_gpa_simulation_files(auth: NEUAuthClient = Depends(require_auth)):
    """
    列出所有GPA模拟文件

    从 data/gpa_simulations/ 目录读取
    """
    try:
        files = []
        for filename in os.listdir(get_gpa_simulation_dir()):
            if filename.endswith('.json'):
                filepath = os.path.join(get_gpa_simulation_dir(), filename)
                stat = os.stat(filepath)

                # 尝试读取统计信息
                stats = None
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        stats = data.get('stats')
                except:
                    pass

                files.append({
                    "filename": filename,
                    "size": stat.st_size,
                    "modified_time": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "stats": stats
                })

        # 按修改时间倒序
        files.sort(key=lambda x: x["modified_time"], reverse=True)
        return files
    except Exception as e:
        _api_logger.error(f"[GPA-Sim] 列出文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"列出文件失败: {str(e)}")


@router.get("/gpa-simulation/file/{filename}")
async def get_gpa_simulation_file(
    filename: str,
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    获取指定GPA模拟文件内容
    """
    try:
        safe_filename = os.path.basename(filename)
        filepath = os.path.join(get_gpa_simulation_dir(), safe_filename)

        if not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="文件不存在")

        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return data
    except HTTPException:
        raise
    except Exception as e:
        _api_logger.error(f"[GPA-Sim] 读取文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取文件失败: {str(e)}")


@router.delete("/gpa-simulation/file/{filename}")
async def delete_gpa_simulation_file(
    filename: str,
    auth: NEUAuthClient = Depends(require_auth)
):
    """
    删除指定GPA模拟文件
    """
    try:
        safe_filename = os.path.basename(filename)
        filepath = os.path.join(get_gpa_simulation_dir(), safe_filename)

        if not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="文件不存在")

        os.remove(filepath)
        _api_logger.info(f"[GPA-Sim] 删除文件: {safe_filename}, user={auth.username}")
        return {"success": True, "message": "文件已删除"}
    except HTTPException:
        raise
    except Exception as e:
        _api_logger.error(f"[GPA-Sim] 删除文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除文件失败: {str(e)}")
