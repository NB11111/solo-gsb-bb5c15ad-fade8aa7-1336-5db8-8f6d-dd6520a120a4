from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlite3 import Connection

from .. import crud
from ..database import get_db
from ..engine import assess_deactivation, build_coverage, build_report
from ..schemas import (
    CoverageReport,
    DeactivationImpactReport,
    DeactivationIn,
    TraceReport,
)

router = APIRouter(prefix="/api", tags=["trace"])


def _report(db: Connection, instrument: str, at: Optional[datetime]) -> dict:
    try:
        return build_report(db, instrument, at)
    except KeyError:
        raise HTTPException(404, f"仪器不存在: {instrument}")


@router.get("/trace/{instrument}", response_model=TraceReport)
def trace_chain(
    instrument: str,
    at: Optional[datetime] = None,
    db: Connection = Depends(get_db),
):
    """链路查询：按指定时点重建该仪器到国家基准的证书链。"""
    return _report(db, instrument, at)


@router.get("/reports/{instrument}", response_model=TraceReport)
def validation_report(
    instrument: str,
    at: Optional[datetime] = None,
    db: Connection = Depends(get_db),
):
    """校验报告：重建结果 + 区间/签名/不确定度的可解释冲突明细。"""
    return _report(db, instrument, at)


@router.get(
    "/coverage/{instrument}",
    response_model=CoverageReport,
    tags=["time-window-coverage"],
)
def coverage_report(
    instrument: str,
    start: datetime = Query(
        ...,
        alias="start",
        description="查询窗起点（UTC，闭区间），如 2026-01-01T00:00:00Z",
    ),
    end: datetime = Query(
        ...,
        alias="end",
        description="查询窗终点（UTC，闭区间），如 2027-01-01T00:00:00Z",
    ),
    db: Connection = Depends(get_db),
):
    """仪器时间窗覆盖查询。

    接收仪器编码与 UTC 起止时刻，按该仪器相关证书与溯源边的有效期边界
    划分时间窗，在每个边界点（闭区间语义）及相邻边界之间的开区间内侧
    各评估一次现有校验报告，返回按时间排列的状态（unique/ambiguous/
    broken）、有效性与冲突码区段。相邻区段仅在状态与冲突码完全相同且
    端点连续时合并；每个区段都用 ``include_start``/``include_end`` 标明
    端点是否包含，闭区间交接点上的瞬时冲突以独立点区段保留，不会被
    两侧区段吞掉。
    """
    try:
        return build_coverage(db, instrument, start, end)
    except KeyError:
        raise HTTPException(404, f"仪器不存在: {instrument}")
    except crud.ValidationError as exc:
        raise HTTPException(422, str(exc))


@router.post(
    "/assessments/certificate-deactivation",
    response_model=DeactivationImpactReport,
    tags=["impact-assessment"],
)
def certificate_deactivation_impact(
    body: DeactivationIn, db: Connection = Depends(get_db)
):
    """证书停用影响评估（dry-run）。

    在不写入数据库的前提下，模拟移除待停用证书及其关联溯源边，按查询时点
    重新评估受影响仪器的唯一链 / 多候选 / 断裂状态，给出停用前后状态、
    替代链摘要与可解释说明；可用 ``instrument_codes`` 限定评估仪器范围。
    """
    try:
        return assess_deactivation(
            db,
            body.at,
            body.certificate_ids,
            body.instrument_codes,
        )
    except crud.ValidationError as exc:
        raise HTTPException(422, str(exc))
