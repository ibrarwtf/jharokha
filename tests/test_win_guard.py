"""The lease file and the last-input clock, with fakes for Win32 input; no input is sent."""

from __future__ import annotations

import subprocess
import sys

import pytest

from jharokha.core.guard import Holder, InputLeased, UserActive

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows guards")


@pytest.fixture
def g():
    from jharokha.win import guard

    return guard


def test_second_process_is_refused_while_the_first_keeps_sending(g, tmp_path):
    now = [1000.0]
    a = g.Lease(123, root=tmp_path, pid=1, clock=lambda: now[0], alive=lambda p: True)
    b = g.Lease(123, root=tmp_path, pid=2, clock=lambda: now[0], alive=lambda p: True)
    a.take()
    now[0] += 30
    with pytest.raises(InputLeased) as e:
        b.take()
    assert e.value.owner_pid == 1 and e.value.idle_s == 30
    a.take()  # sending again renews it
    now[0] += 59
    with pytest.raises(InputLeased):
        b.take()
    now[0] += 2
    b.take()  # lapsed: b holds it now
    assert a.holder() == Holder(2, now[0])


def test_leases_are_per_window_and_name_no_title(g, tmp_path):
    g.Lease("Session A", root=tmp_path, pid=1).take()
    g.Lease("Session B", root=tmp_path, pid=2).take()
    names = sorted(p.name for p in tmp_path.iterdir())
    assert len(names) == 2 and not any("Session" in n for n in names)


def test_release_frees_only_our_own_lease(g, tmp_path):
    a = g.Lease(1, root=tmp_path, pid=1, alive=lambda p: True)
    b = g.Lease(1, root=tmp_path, pid=2, alive=lambda p: True)
    a.take()
    b.release()
    assert a.holder() is not None
    a.release()
    assert a.holder() is None
    b.take()


def test_a_corrupt_lease_file_holds_nothing(g, tmp_path):
    lease = g.Lease(1, root=tmp_path, pid=1)
    tmp_path.joinpath(lease.path).write_bytes(b"{not json")
    assert lease.holder() is None
    lease.take()


def test_live_owner_blocks_and_its_exit_frees_the_window(g, tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        g.Lease(7, root=tmp_path, pid=child.pid).take()
        with pytest.raises(InputLeased, match=f"process {child.pid}"):
            g.Lease(7, root=tmp_path).take()
    finally:
        child.kill()
        child.wait()
    g.Lease(7, root=tmp_path).take()  # a crashed owner no longer blocks


class FakeClock:
    """GetTickCount and GetLastInputInfo from plain numbers."""

    def __init__(self) -> None:
        self.now = 50_000
        self.last_input = 0

    def GetTickCount(self) -> int:  # noqa: N802
        return self.now

    def GetLastInputInfo(self, info) -> int:  # noqa: N802
        info._obj.dwTime = self.last_input
        return 1


@pytest.fixture
def clock(g, monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(g, "kernel32", c)
    monkeypatch.setattr(g, "user32", c)
    monkeypatch.setattr(g, "_own_input_ms", None)
    return c


def test_our_own_send_does_not_count_as_the_user(g, clock):
    clock.last_input = clock.now = 50_000
    g.record_own_input()
    clock.now = 50_500
    assert g.user_idle_s() is None
    g.ensure_user_idle()


def test_a_shell_waits_for_the_user_to_go_idle_and_the_server_does_not(monkeypatch):
    from jharokha.win import source

    s = source.WindowsSource.__new__(source.WindowsSource)  # no DPI or engine setup
    answers = [UserActive(1.0, 3), UserActive(2.0, 3), None]

    def ensure() -> None:
        if (e := answers.pop(0)) is not None:
            raise e

    monkeypatch.setattr(source.time, "sleep", lambda s: None)
    s.wait_idle_s = 10.0
    s._wait_idle(ensure)
    assert answers == []
    s.wait_idle_s = 0.0
    answers[:] = [UserActive(1.0, 3), None]
    with pytest.raises(UserActive):
        s._wait_idle(ensure)


def test_status_sees_another_holder_without_taking_the_lease(g, tmp_path):
    g.Lease(9, root=tmp_path, pid=2, alive=lambda p: True).take()
    mine = g.Lease(9, root=tmp_path, pid=1, alive=lambda p: True)
    other = mine.other()
    assert other is not None and other.owner_pid == 2
    assert mine.holder() is not None and mine.holder().pid == 2  # type: ignore[union-attr]
    assert g.Lease(9, root=tmp_path, pid=2).other() is None


def test_user_input_after_ours_refuses(g, clock):
    clock.now = 50_000
    g.record_own_input()
    clock.last_input, clock.now = 51_000, 52_000
    assert g.user_idle_s() == 1.0
    with pytest.raises(UserActive):
        g.ensure_user_idle()
    clock.now = 55_000
    g.ensure_user_idle()
