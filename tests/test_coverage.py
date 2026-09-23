"""仪器时间窗覆盖查询（GET /api/coverage/{instrument}）测试。"""
from fastapi.testclient import TestClient  # noqa: F401  保证 client fixture 依赖已加载

from tests.test_trace import (
    AT,
    _certs,
    _edges,
    _instruments,
    _submit,
)

COVERAGE = "/api/coverage/W"


def _get(client, start, end, instrument="W"):
    return client.get(
        f"/api/coverage/{instrument}"
        f"?start={start}&end={end}"
    )


def test_single_segment_covers_whole_window_within_validity(client):
    _submit(client)
    # 查询窗完全落在 CW/CR/CG 全部有效期之内：合并为单条 unique 区段
    r = _get(client, "2026-02-01T00:00:00", "2026-06-01T00:00:00")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["segment_count"] == 1
    seg = d["segments"][0]
    assert (seg["start"], seg["end"]) == (
        "2026-02-01T00:00:00Z",
        "2026-06-01T00:00:00Z",
    )
    assert seg["include_start"] is True and seg["include_end"] is True
    assert seg["resolution"] == "unique"
    assert seg["valid"] is True
    assert seg["conflict_codes"] == []
    # 窗内无证书/边边界，仅窗口两端：点 + 开区间 + 点，评估 3 次后合并为 1 段
    assert d["evaluated_at_count"] == 3
    assert d["boundary_points"] == [
        "2026-02-01T00:00:00Z",
        "2026-06-01T00:00:00Z",
    ]


def test_closed_handover_instant_conflict_is_not_swallowed(client):
    # CW1 止于 2026-06-01（闭），CW2 始于同一时刻（闭）：
    # 交接瞬间两张证书同时有效 -> MULTIPLE_ACTIVE_CERTIFICATES，
    # 必须作为独立点区段保留，不能并入两侧 unique 开区间。
    insts = _instruments()
    certs = [
        c for c in _certs() if c["id"] != "CW"
    ] + [
        {"id": "CW1", "instrument_code": "W", "cert_number": "W-1",
         "issued_at": "2025-07-01T00:00:00",
         "valid_from": "2026-01-01T00:00:00",
         "valid_until": "2026-06-01T00:00:00",
         "uncertainty": 0.01, "unit": "mm"},
        {"id": "CW2", "instrument_code": "W", "cert_number": "W-2",
         "issued_at": "2026-07-01T00:00:00",
         "valid_from": "2026-06-01T00:00:00",
         "valid_until": "2027-01-01T00:00:00",
         "uncertainty": 0.02, "unit": "mm"},
    ]
    edges = [
        {"child_cert_id": "CW1", "parent_cert_id": "CR"},
        {"child_cert_id": "CW2", "parent_cert_id": "CR"},
        {"child_cert_id": "CR", "parent_cert_id": "CG"},
    ]
    _submit(client, insts, certs, edges)

    d = _get(client, "2026-01-01T00:00:00", "2027-01-01T00:00:00").json()
    assert d["segment_count"] == 3
    before, point, after = d["segments"]

    assert (before["start"], before["end"], before["resolution"]) == (
        "2026-01-01T00:00:00Z", "2026-06-01T00:00:00Z", "unique")
    assert before["include_start"] is True and before["include_end"] is False

    assert point["start"] == point["end"] == "2026-06-01T00:00:00Z"
    assert point["include_start"] is True and point["include_end"] is True
    assert point["resolution"] == "ambiguous"
    assert point["valid"] is False
    assert "MULTIPLE_ACTIVE_CERTIFICATES" in point["conflict_codes"]

    assert (after["start"], after["end"], after["resolution"]) == (
        "2026-06-01T00:00:00Z", "2027-01-01T00:00:00Z", "unique")
    assert after["include_start"] is False and after["include_end"] is True
    # 交接时刻出现在边界点列表中
    assert "2026-06-01T00:00:00Z" in d["boundary_points"]


def test_gap_window_splits_at_history_gap(client):
    # T-1 止于 2025-06-30，T-2 始于 2025-09-01：中间断档 + 两侧状态不同
    insts = [
        {"code": "TP", "name": "参考温度计"},
        {"code": "T", "name": "工作热电偶"},
    ]
    certs = [
        {"id": "CTP", "instrument_code": "TP", "cert_number": "TP-1",
         "issued_at": "2025-01-01T00:00:00",
         "valid_from": "2025-01-01T00:00:00", "valid_until": "2027-01-01T00:00:00",
         "uncertainty": 0.05, "unit": "C"},
        {"id": "CT-1", "instrument_code": "T", "cert_number": "T-1",
         "issued_at": "2025-01-05T00:00:00",
         "valid_from": "2025-01-01T00:00:00", "valid_until": "2025-06-30T00:00:00",
         "uncertainty": 0.5, "unit": "C"},
        {"id": "CT-2", "instrument_code": "T", "cert_number": "T-2",
         "issued_at": "2025-09-01T00:00:00",
         "valid_from": "2025-09-01T00:00:00", "valid_until": "2026-09-01T00:00:00",
         "uncertainty": 0.4, "unit": "C"},
    ]
    edges = [{"child_cert_id": "CT-2", "parent_cert_id": "CTP"}]
    _submit(client, insts, certs, edges)

    d = _get(
        client, "2025-06-30T00:00:00", "2025-09-01T00:00:00", instrument="T"
    ).json()
    resolutions = [
        (s["start"], s["include_start"], s["end"], s["include_end"],
         s["resolution"], s["valid"])
        for s in d["segments"]
    ]
    assert resolutions == [
        ("2025-06-30T00:00:00Z", True, "2025-06-30T00:00:00Z", True,
         "broken", False),
        ("2025-06-30T00:00:00Z", False, "2025-09-01T00:00:00Z", False,
         "broken", False),
        ("2025-09-01T00:00:00Z", True, "2025-09-01T00:00:00Z", True,
         "broken", False),
    ]
    gap_seg = d["segments"][1]
    assert "NO_ACTIVE_CERTIFICATE" in gap_seg["conflict_codes"]
    assert "INTERVAL_GAP" in gap_seg["conflict_codes"]


def test_edge_validity_boundaries_split_segments(client):
    # CW->CR 边自定义区间 [2026-02-01, 2026-11-01]：
    # 边外为死路、边内成链，且边边界点与其内侧同状态应被吸收合并
    edges = [
        {"child_cert_id": "CW", "parent_cert_id": "CR",
         "valid_from": "2026-02-01T00:00:00",
         "valid_until": "2026-11-01T00:00:00"},
        {"child_cert_id": "CR", "parent_cert_id": "CG"},
    ]
    _submit(client, edges=edges)

    d = _get(client, "2026-01-01T00:00:00", "2027-01-01T00:00:00").json()
    assert d["segment_count"] == 3
    before, middle, after = d["segments"]

    assert before["resolution"] == "broken"
    assert (before["start"], before["end"]) == (
        "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")
    assert before["include_start"] is True and before["include_end"] is False

    assert middle["resolution"] == "unique"
    assert (middle["start"], middle["end"]) == (
        "2026-02-01T00:00:00Z", "2026-11-01T00:00:00Z")
    assert middle["include_start"] is True and middle["include_end"] is True

    assert after["resolution"] == "broken"
    assert (after["start"], after["end"]) == (
        "2026-11-01T00:00:00Z", "2027-01-01T00:00:00Z")
    assert after["include_start"] is False and after["include_end"] is True
    # 边边界（而非仅证书边界）参与划分
    assert set(d["statistics"]["relevant_edge_ids"])  # 非空
    assert d["boundary_points"].count("2026-02-01T00:00:00Z") == 1
    assert d["boundary_points"].count("2026-11-01T00:00:00Z") == 1


def test_segments_are_time_ordered_and_contiguous(client):
    _submit(client)
    d = _get(client, "2025-06-01T00:00:00", "2027-06-01T00:00:00").json()
    segs = d["segments"]
    assert segs[0]["start"] == "2025-06-01T00:00:00Z"
    assert segs[-1]["end"] == "2027-06-01T00:00:00Z"
    for prev, nxt in zip(segs, segs[1:]):
        assert prev["end"] == nxt["start"]
        assert prev["start"] <= prev["end"]
    # 状态随时间：无证书(broken) -> unique -> 父证失效(broken) -> CW 也失效(broken)
    assert segs[0]["resolution"] == "broken"
    joined = [s["resolution"] for s in segs]
    assert "unique" in joined
    assert joined[-1] == "broken"
    assert all(s["valid"] == (s["resolution"] == "unique") for s in segs)


def test_instant_window_returns_single_point_segment(client):
    _submit(client)
    # start == end：退化为单点查询
    d = _get(client, AT, AT).json()
    assert d["segment_count"] == 1
    seg = d["segments"][0]
    assert seg["start"] == seg["end"] == AT + "Z"
    assert seg["include_start"] is True and seg["include_end"] is True
    assert seg["resolution"] == "unique" and seg["valid"] is True
    assert d["evaluated_at_count"] == 1
    assert d["boundary_points"] == [AT + "Z"]


def test_inverted_window_returns_422(client):
    _submit(client)
    r = _get(client, "2027-01-01T00:00:00", "2026-01-01T00:00:00")
    assert r.status_code == 422
    assert "start" in r.json()["detail"]


def test_coverage_missing_instrument_404(client):
    r = _get(
        client, "2026-01-01T00:00:00", "2027-01-01T00:00:00", instrument="NOPE"
    )
    assert r.status_code == 404


def test_coverage_requires_start_and_end(client):
    _submit(client)
    assert client.get("/api/coverage/W").status_code == 422
    assert client.get("/api/coverage/W?start=2026-01-01T00:00:00Z").status_code == 422
