import curses
import enum
from typing import List
from typing import Optional
from typing import TYPE_CHECKING
from typing import Union

from babi.horizontal_scrolling import line_x
from babi.horizontal_scrolling import scrolled_line

if TYPE_CHECKING:
    from babi.main import Screen  # XXX: circular

PromptResult = enum.Enum('PromptResult', 'CANCELLED')


class Prompt:
    def __init__(self, screen: 'Screen', prompt: str, lst: List[str]) -> None:
        self._screen = screen
        self._prompt = prompt
        self._lst = lst
        self._y = len(lst) - 1
        self._x = len(self._s)

    @property
    def _s(self) -> str:
        return self._lst[self._y]

    @_s.setter
    def _s(self, s: str) -> None:
        self._lst[self._y] = s

    def _render_prompt(self, *, base: Optional[str] = None) -> None:
        base = base or self._prompt
        if not base or curses.COLS < 7:
            prompt_s = ''
        elif len(base) > curses.COLS - 6:
            prompt_s = f'{base[:curses.COLS - 7]}…: '
        else:
            prompt_s = f'{base}: '
        width = curses.COLS - len(prompt_s)
        line = scrolled_line(self._s, self._x, width)
        cmd = f'{prompt_s}{line}'
        self._screen.stdscr.insstr(curses.LINES - 1, 0, cmd, curses.A_REVERSE)
        x = len(prompt_s) + self._x - line_x(self._x, width)
        self._screen.stdscr.move(curses.LINES - 1, x)

    def _up(self) -> None:
        self._y = max(0, self._y - 1)
        self._x = len(self._s)

    def _down(self) -> None:
        self._y = min(len(self._lst) - 1, self._y + 1)
        self._x = len(self._s)

    def _right(self) -> None:
        self._x = min(len(self._s), self._x + 1)

    def _left(self) -> None:
        self._x = max(0, self._x - 1)

    def _home(self) -> None:
        self._x = 0

    def _end(self) -> None:
        self._x = len(self._s)

    def _ctrl_left(self) -> None:
        if self._x <= 1:
            self._x = 0
        else:
            self._x -= 1
            tp = self._s[self._x - 1].isalnum()
            while self._x > 0 and tp == self._s[self._x - 1].isalnum():
                self._x -= 1

    def _ctrl_right(self) -> None:
        if self._x >= len(self._s) - 1:
            self._x = len(self._s)
        else:
            self._x += 1
            tp = self._s[self._x].isalnum()
            while self._x < len(self._s) and tp == self._s[self._x].isalnum():
                self._x += 1

    def _backspace(self) -> None:
        if self._x > 0:
            self._s = self._s[:self._x - 1] + self._s[self._x:]
            self._x -= 1

    def _delete(self) -> None:
        if self._x < len(self._s):
            self._s = self._s[:self._x] + self._s[self._x + 1:]

    def _cut_to_end(self) -> None:
        self._s = self._s[:self._x]

    def _resize(self) -> None:
        self._screen.resize()

    def _reverse_search(self) -> Union[None, str, PromptResult]:
        reverse_s = ''
        reverse_idx = self._y
        while True:
            reverse_failed = False
            for search_idx in range(reverse_idx, -1, -1):
                if reverse_s in self._lst[search_idx]:
                    reverse_idx = self._y = search_idx
                    self._x = self._lst[search_idx].index(reverse_s)
                    break
            else:
                reverse_failed = True

            if reverse_failed:
                base = f'{self._prompt}(failed reverse-search)`{reverse_s}`'
            else:
                base = f'{self._prompt}(reverse-search)`{reverse_s}`'

            self._render_prompt(base=base)

            key = self._screen.get_char()
            if key.keyname == b'KEY_RESIZE':
                self._screen.resize()
            elif key.keyname == b'KEY_BACKSPACE' or key.keyname == b'^H':
                reverse_s = reverse_s[:-1]
            elif isinstance(key.wch, str) and key.wch.isprintable():
                reverse_s += key.wch
            elif key.keyname == b'^R':
                reverse_idx = max(0, reverse_idx - 1)
            elif key.keyname == b'^C':
                return self._screen.status.cancelled()
            elif key.keyname == b'^M':
                return self._s
            else:
                self._x = len(self._s)
                return None

    def _cancel(self) -> PromptResult:
        return self._screen.status.cancelled()

    def _submit(self) -> str:
        return self._s

    DISPATCH = {
        # movement
        b'KEY_UP': _up,
        b'KEY_DOWN': _down,
        b'KEY_RIGHT': _right,
        b'KEY_LEFT': _left,
        b'KEY_HOME': _home,
        b'^A': _home,
        b'KEY_END': _end,
        b'^E': _end,
        b'kRIT5': _ctrl_right,
        b'kLFT5': _ctrl_left,
        # editing
        b'KEY_BACKSPACE': _backspace,
        b'^H': _backspace,  # ^Backspace
        b'KEY_DC': _delete,
        b'^K': _cut_to_end,
        # misc
        b'KEY_RESIZE': _resize,
        b'^R': _reverse_search,
        b'^M': _submit,
        b'^C': _cancel,
    }

    def _c(self, c: str) -> None:
        self._s = self._s[:self._x] + c + self._s[self._x:]
        self._x += 1

    def run(self) -> Union[PromptResult, str]:
        while True:
            self._render_prompt()

            key = self._screen.get_char()
            if key.keyname in Prompt.DISPATCH:
                ret = Prompt.DISPATCH[key.keyname](self)
                if ret is not None:
                    return ret
            elif isinstance(key.wch, str) and key.wch.isprintable():
                self._c(key.wch)
