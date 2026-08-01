import os
import json
import tempfile
import hashlib
import shutil
from pathlib import Path
from typing import List
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends

from backend.app.dependencies import _storage, _api_logger, get_gpa_simulation_dir
from backend.app.schemas import GPASimulationExportRequest, GPASimulationFile
from backend.core.auth import NEUAuthClient
from backend.app.dependencies import require_cached_auth_identity
from backend.core.runtime.config import secure_file
from backend.core.log import log_application_error

router = APIRouter()


def _account_directory(username: str) -> Path:
    account_key = hashlib.sha256(str(username).encode("utf-8")).hexdigest()[:24]
    directory = Path(get_gpa_simulation_dir()) / "gpa_simulations" / account_key
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _migrate_owned_legacy_files(username: str) -> None:
    """Copy trusted legacy documents without modifying their original files."""
    legacy_root = Path(get_gpa_simulation_dir())
    destination = _account_directory(username)
    for source in legacy_root.glob("*.json"):
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            if data.get("export_info", {}).get("exported_by") != username:
                continue
            target = destination / source.name
            if not target.exists():
                shutil.copy2(source, target)
                secure_file(target)
        except (OSError, ValueError, TypeError):
            continue


def _read_owned_file(filepath: str, username: str) -> dict:
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    owner = data.get("export_info", {}).get("exported_by")
    if owner != username:
        raise HTTPException(status_code=404, detail="文件不存在")
    return data


@router.post("/gpa-simulation/export")
def export_gpa_simulation(
    request: GPASimulationExportRequest,
    auth: NEUAuthClient = Depends(require_cached_auth_identity)
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

        directory = _account_directory(auth.username)
        filepath = str(directory / safe_filename)

        # 添加导出元数据
        export_data = {
            **request.data,
            "export_info": {
                "exported_by": auth.username,
                "exported_at": datetime.now().isoformat(),
                "version": "2.0"
            }
        }

        fd, temporary_name = tempfile.mkstemp(prefix=".gpa-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            secure_file(Path(temporary_name))
            os.replace(temporary_name, filepath)
            secure_file(Path(filepath))
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

        _api_logger.info(f"[GPA-Sim] 导出成功: {safe_filename}, user={auth.username}")
        return {
            "success": True,
            "filename": safe_filename,
            "path": filepath
        }
    except Exception as e:
        error_id = log_application_error("gpa.export", e, 500)
        raise HTTPException(status_code=500, detail=f"导出失败（错误编号：{error_id}）") from e


@router.get("/gpa-simulation/files", response_model=List[GPASimulationFile])
def list_gpa_simulation_files(auth: NEUAuthClient = Depends(require_cached_auth_identity)):
    """
    列出所有GPA模拟文件

    从 data/gpa_simulations/ 目录读取
    """
    try:
        _migrate_owned_legacy_files(auth.username)
        directory = _account_directory(auth.username)
        files = []
        for filename in os.listdir(directory):
            if filename.endswith('.json'):
                filepath = str(directory / filename)
                stat = os.stat(filepath)

                # 尝试读取统计信息
                stats = None
                try:
                    data = _read_owned_file(filepath, auth.username)
                    stats = data.get('stats')
                except HTTPException:
                    continue
                except (OSError, ValueError, TypeError):
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
        error_id = log_application_error("gpa.list_files", e, 500)
        raise HTTPException(status_code=500, detail=f"列出文件失败（错误编号：{error_id}）") from e


@router.get("/gpa-simulation/file/{filename}")
def get_gpa_simulation_file(
    filename: str,
    auth: NEUAuthClient = Depends(require_cached_auth_identity)
):
    """
    获取指定GPA模拟文件内容
    """
    try:
        safe_filename = os.path.basename(filename)
        _migrate_owned_legacy_files(auth.username)
        filepath = str(_account_directory(auth.username) / safe_filename)

        if not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="文件不存在")

        return _read_owned_file(filepath, auth.username)
    except HTTPException:
        raise
    except Exception as e:
        error_id = log_application_error("gpa.read_file", e, 500)
        raise HTTPException(status_code=500, detail=f"读取文件失败（错误编号：{error_id}）") from e


@router.delete("/gpa-simulation/file/{filename}")
def delete_gpa_simulation_file(
    filename: str,
    auth: NEUAuthClient = Depends(require_cached_auth_identity)
):
    """
    删除指定GPA模拟文件
    """
    try:
        safe_filename = os.path.basename(filename)
        _migrate_owned_legacy_files(auth.username)
        filepath = str(_account_directory(auth.username) / safe_filename)

        if not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="文件不存在")

        _read_owned_file(filepath, auth.username)
        os.remove(filepath)
        _api_logger.info(f"[GPA-Sim] 删除文件: {safe_filename}, user={auth.username}")
        return {"success": True, "message": "文件已删除"}
    except HTTPException:
        raise
    except Exception as e:
        error_id = log_application_error("gpa.delete_file", e, 500)
        raise HTTPException(status_code=500, detail=f"删除文件失败（错误编号：{error_id}）") from e
