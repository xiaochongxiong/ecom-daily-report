"""推送决策的纯函数测试。

为什么能这么测：decide_push() 不碰网络、不碰数据库，
只吃一个 alerts 列表、吐一个 (布尔, 原因)。
一旦掺进 requests.post，这里就只能靠 mock，测试立刻变脆。
"""
import pytest
import requests

from src import notify
from src.analyze import Alert
from src.notify import decide_push


def _alert(level: str) -> Alert:
    """造一条最小可用的预警，只关心 level。"""
    return Alert(
        level=level,
        alert_type="test",
        sku_id="SKU-001",
        sku_name="测试商品",
        message="测试预警",
    )


def test_always_pushes_even_without_alerts() -> None:
    # 每天必发：让业务方知道"系统活着"，哪怕全绿
    assert decide_push([], "always") == (True, "always")


def test_high_only_skips_when_no_high_alert() -> None:
    # 只有 mid / low 不该打扰人；空列表同理
    assert decide_push([_alert("mid"), _alert("low")], "high_only") == (False, "no_high_alert")
    assert decide_push([], "high_only") == (False, "no_high_alert")


def test_high_only_pushes_when_high_exists() -> None:
    assert decide_push([_alert("mid"), _alert("high")], "high_only") == (True, "has_high_alert")


def test_unknown_policy_raises() -> None:
    # 配置写错要立刻炸，而不是默默按某个默认值继续跑
    with pytest.raises(ValueError, match="未知推送策略"):
        decide_push([], "high_ony")

def test_unknown_policy_raises2() -> None:
    # 配置写错要立刻炸，而不是默默按某个默认值继续跑
    try:
        decide_push([], "high_ony")
    except ValueError as e:
        assert "未知推送策略" in str(e)
    else:
        pytest.fail("应该抛 ValueError，但没抛")


class _Resp:
    """假 response，send_feishu 只用到 status_code / content / json()。"""

    def __init__(self, status: int, body: dict | None = None) -> None:
        self.status_code = status
        self._body = body if body is not None else {"code": 0}
        self.content = b"{}"

    def json(self) -> dict:
        return self._body


def test_send_feishu_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """网络抖动两次、第三次成功 → 最终结果必须是 ok，不能丢推送。

    真实触发场景：容器冷启动首次 POST 读超时 10s，同一份代码重跑 0.6s 成功。
    """
    calls: list[int] = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise requests.exceptions.ReadTimeout("read timed out")
        return _Resp(200)

    monkeypatch.setattr(notify.requests, "post", fake_post)
    monkeypatch.setattr(notify.time, "sleep", lambda _s: None)  # 别真等 1+3 秒

    assert notify.send_feishu("报表", webhook="http://fake") == notify.PushResult.OK
    assert len(calls) == 3


def test_send_feishu_does_not_retry_on_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """webhook 失效是配置问题，重试解决不了，只该试一次。"""
    calls: list[int] = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return _Resp(400, {"code": 19002, "msg": "invalid webhook"})

    monkeypatch.setattr(notify.requests, "post", fake_post)
    monkeypatch.setattr(notify.time, "sleep", lambda _s: None)

    assert notify.send_feishu("报表", webhook="http://fake") == notify.PushResult.FAILED
    assert len(calls) == 1


def test_send_feishu_gives_up_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """三次全失败要返回 failed（落库可查），且不能无限重试。"""
    calls: list[int] = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return _Resp(500, {"code": 500, "msg": "internal"})

    monkeypatch.setattr(notify.requests, "post", fake_post)
    monkeypatch.setattr(notify.time, "sleep", lambda _s: None)

    assert notify.send_feishu("报表", webhook="http://fake") == notify.PushResult.FAILED
    assert len(calls) == notify._MAX_ATTEMPTS
