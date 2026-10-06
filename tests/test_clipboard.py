"""유니코드/줄바꿈과 Windows 메모리 소유권을 검증한다. 실제 클립보드는 변경하지 않는다."""
import ctypes
from unittest.mock import Mock

import pytest

from src.util import clipboard as CB


@pytest.fixture
def api(monkeypatch):
    user, kernel = Mock(), Mock()
    data = ctypes.create_string_buffer(512)
    kernel.GlobalAlloc.return_value = 0x100000001
    kernel.GlobalLock.return_value = ctypes.addressof(data)
    user.GetDesktopWindow.return_value = 0x200000001
    user.OpenClipboard.return_value = True
    user.EmptyClipboard.return_value = True
    user.SetClipboardData.return_value = kernel.GlobalAlloc.return_value
    monkeypatch.setattr(CB, "_clipboard_api", lambda: (user, kernel))
    monkeypatch.setattr(CB.os, "name", "nt")
    monkeypatch.setattr(CB.time, "sleep", Mock())
    return user, kernel, data


def test_unicode_copy_preserves_blank_lines_and_transfers_memory(api):
    user, kernel, data = api
    CB.copy_text("한글😀\n\n다음\r끝")
    expected = "한글😀\r\n\r\n다음\r\n끝\0".encode("utf-16-le")
    assert data.raw[:len(expected)] == expected
    kernel.GlobalAlloc.assert_called_once_with(2, len(expected))
    user.SetClipboardData.assert_called_once_with(13, 0x100000001)
    user.OpenClipboard.assert_called_once_with(0x200000001)
    kernel.GlobalFree.assert_not_called()
    user.CloseClipboard.assert_called_once()


def test_temporarily_busy_clipboard_retries(api):
    user, kernel, data = api
    user.OpenClipboard.side_effect = [False, False, True]
    CB.copy_text("설명")
    assert user.OpenClipboard.call_count == 3
    assert CB.time.sleep.call_count == 2
    kernel.GlobalFree.assert_not_called()


@pytest.mark.parametrize("failure", ["lock", "open", "empty", "set"])
def test_copy_failure_releases_allocated_memory(api, failure):
    user, kernel, data = api
    targets = {"lock": kernel.GlobalLock, "open": user.OpenClipboard,
               "empty": user.EmptyClipboard, "set": user.SetClipboardData}
    targets[failure].return_value = 0
    with pytest.raises(RuntimeError):
        CB.copy_text("설명")
    kernel.GlobalFree.assert_called_once_with(0x100000001)
    assert user.CloseClipboard.call_count == (1 if failure in ("empty", "set") else 0)
    if failure == "open":
        assert user.OpenClipboard.call_count == 20


def test_allocation_failure_never_opens_clipboard(api):
    user, kernel, data = api
    kernel.GlobalAlloc.return_value = 0
    with pytest.raises(RuntimeError, match="할당"):
        CB.copy_text("설명")
    user.OpenClipboard.assert_not_called()
    kernel.GlobalFree.assert_not_called()
