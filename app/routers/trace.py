from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlite3 import Connection

from .. import crud
from ..database import get_db
from ..engine import assess_deactivation, build_report, build_window_report
from ..schemas import (
    CoverageWindowReport,
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
    response_model=CoverageWindowReport,
    tags=["time-window-coverage"],
)
def instrument_time_window_coverage(
    instrument: str,
    start: datetime = Query(
        ...,
        alias="start",
        description="查询窗起点（UTC，含），如 2026-01-01T00:00:00Z",
    ),
    end: datetime = Query(
        ...,
        alias="end",
        description="查询窗终点（UTC，含），如 2026-12-31T23:59:59Z",
    ),
    db: Connection = Depends(get_db),
):
    """仪器时间窗覆盖查询：按有效期边界划分区段并逐段给出校验状态。

    以仪器编码与 UTC 闭时间窗 ``[start, end]`` 为输入：收集该仪器相关
    证书及溯源边落在窗内的全部有效期边界（valid_from/valid_until），
    在每个**边界点**（闭区间语义）与每对相邻边界之间的**开区间内部**
    各运行一次现有时点校验报告；再将结果按时间排列，仅当状态
    （resolution）、有效性（valid）与冲突码多重集完全相同且端点
    严格连续（一开一闭相接）时合并为一个区段。每段显式标明
    ``start_inclusive`` / ``end_inclusive``，因此证书首尾相接（闭区间）
    时交接点上的瞬时冲突（如瞬时双重有效）不会被相邻区段吞掉。
    """
    try:
        return build_window_report(db, instrument, start, end)
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
