import contextlib
import curses
import enum
import hashlib
import os
import re
import signal
import sys
from typing import Callable
from typing import Generator
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Pattern
from typing import Tuple
from typing import Union

from babi.file import Action
from babi.file import File
from babi.file import get_lines
from babi.history import History
from babi.margin import Margin
from babi.perf import Perf
from babi.prompt import Prompt
from babi.prompt import PromptResult
from babi.status import Status

VERSION_STR = 'babi v0'
EditResult = enum.Enum('EditResult', 'EXIT NEXT PREV')

# TODO: find a place to populate these, surely there's a database somewhere
SEQUENCE_KEYNAME = {
    '\x1bOH': b'KEY_HOME',
    '\x1bOF': b'KEY_END',
    '\x1b[1;2A': b'KEY_SR',
    '\x1b[1;2B': b'KEY_SF',
    '\x1b[1;2C': b'KEY_SRIGHT',
    '\x1b[1;2D': b'KEY_SLEFT',
    '\x1b[1;2H': b'KEY_SHOME',
    '\x1b[1;2F': b'KEY_SEND',
    '\x1b[5;2~': b'KEY_SPREVIOUS',
    '\x1b[6;2~': b'KEY_SNEXT',
    '\x1b[1;3A': b'kUP3',  # M-Up
    '\x1b[1;3B': b'kDN3',  # M-Down
    '\x1b[1;3C': b'kRIT3',  # M-Right
    '\x1b[1;3D': b'kLFT3',  # M-Left
    '\x1b[1;5A': b'kUP5',  # ^Up
    '\x1b[1;5B': b'kDN5',  # ^Down
    '\x1b[1;5C': b'kRIT5',  # ^Right
    '\x1b[1;5D': b'kLFT5',  # ^Left
    '\x1b[1;5H': b'kHOM5',  # ^Home
    '\x1b[1;5F': b'kEND5',  # ^End
    '\x1b[1;6C': b'kRIT6',  # Shift + ^Right
    '\x1b[1;6D': b'kLFT6',  # Shift + ^Left
    '\x1b[1;6H': b'kHOM6',  # Shift + ^Home
    '\x1b[1;6F': b'kEND6',  # Shift + ^End
}


class Key(NamedTuple):
    wch: Union[int, str]
    keyname: bytes


class Screen:
    def __init__(
            self,
            stdscr: 'curses._CursesWindow',
            files: List[File],
    ) -> None:
        self.stdscr = stdscr
        self.files = files
        self.i = 0
        self.history = History()
        self.perf = Perf()
        self.status = Status()
        self.margin = Margin.from_current_screen()
        self.cut_buffer: Tuple[str, ...] = ()
        self.cut_selection = False
        self._resize_cb: Optional[Callable[[], None]] = None

    @property
    def file(self) -> File:
        return self.files[self.i]

    def _draw_header(self) -> None:
        filename = self.file.filename or '<<new file>>'
        if self.file.modified:
            filename += ' *'
        if len(self.files) > 1:
            files = f'[{self.i + 1}/{len(self.files)}] '
            version_width = len(VERSION_STR) + 2 + len(files)
        else:
            files = ''
            version_width = len(VERSION_STR) + 2
        centered = filename.center(curses.COLS)[version_width:]
        s = f' {VERSION_STR} {files}{centered}{files}'
        self.stdscr.insstr(0, 0, s, curses.A_REVERSE)

    def _get_char(self) -> Key:
        wch = self.stdscr.get_wch()
        if isinstance(wch, str) and wch == '\x1b':
            self.stdscr.nodelay(True)
            try:
                while True:
                    try:
                        new_wch = self.stdscr.get_wch()
                        if isinstance(new_wch, str):
                            wch += new_wch
                        else:  # pragma: no cover (impossible?)
                            curses.unget_wch(new_wch)
                            break
                    except curses.error:
                        break
            finally:
                self.stdscr.nodelay(False)

            if len(wch) == 2:
                return Key(wch, f'M-{wch[1]}'.encode())
            elif len(wch) > 1:
                keyname = SEQUENCE_KEYNAME.get(wch, b'unknown')
                return Key(wch, keyname)
        elif wch == '\x7f':  # pragma: no cover (macos)
            keyname = curses.keyname(curses.KEY_BACKSPACE)
            return Key(wch, keyname)

        key = wch if isinstance(wch, int) else ord(wch)
        keyname = curses.keyname(key)
        return Key(wch, keyname)

    def get_char(self) -> Key:
        self.perf.end()
        ret = self._get_char()
        self.perf.start(ret.keyname.decode())
        return ret

    def draw(self) -> None:
        if self.margin.header:
            self._draw_header()
        self.file.draw(self.stdscr, self.margin)
        self.status.draw(self.stdscr, self.margin)

    @contextlib.contextmanager
    def resize_cb(self, f: Callable[[], None]) -> Generator[None, None, None]:
        assert self._resize_cb is None, self._resize_cb
        self._resize_cb = f
        try:
            yield
        finally:
            self._resize_cb = None

    def resize(self) -> None:
        curses.update_lines_cols()
        self.margin = Margin.from_current_screen()
        self.file.scroll_screen_if_needed(self.margin)
        self.draw()
        if self._resize_cb is not None:
            self._resize_cb()

    def quick_prompt(self, prompt: str, opts: str) -> Union[str, PromptResult]:
        while True:
            s = prompt.ljust(curses.COLS)
            if len(s) > curses.COLS:
                s = f'{s[:curses.COLS - 1]}…'
            self.stdscr.insstr(curses.LINES - 1, 0, s, curses.A_REVERSE)
            x = min(curses.COLS - 1, len(prompt) + 1)
            self.stdscr.move(curses.LINES - 1, x)

            key = self.get_char()
            if key.keyname == b'KEY_RESIZE':
                self.resize()
            elif key.keyname == b'^C':
                return self.status.cancelled()
            elif isinstance(key.wch, str) and key.wch in opts:
                return key.wch

    def prompt(
            self,
            prompt: str,
            *,
            allow_empty: bool = False,
            history: Optional[str] = None,
            default_prev: bool = False,
            default: Optional[str] = None,
    ) -> Union[str, PromptResult]:
        default = default or ''
        self.status.clear()
        if history is not None:
            history_data = [*self.history.data[history], default]
            if default_prev and history in self.history.prev:
                prompt = f'{prompt} [{self.history.prev[history]}]'
        else:
            history_data = [default]

        ret = Prompt(self, prompt, history_data).run()

        if ret is not PromptResult.CANCELLED and history is not None:
            if ret:  # only put non-empty things in history
                history_lst = self.history.data[history]
                if not history_lst or history_lst[-1] != ret:
                    history_lst.append(ret)
                self.history.prev[history] = ret
            elif default_prev and history in self.history.prev:
                return self.history.prev[history]

        if not allow_empty and not ret:
            return self.status.cancelled()
        else:
            return ret

    def go_to_line(self) -> None:
        response = self.prompt('enter line number')
        if response is not PromptResult.CANCELLED:
            try:
                lineno = int(response)
            except ValueError:
                self.status.update(f'not an integer: {response!r}')
            else:
                self.file.go_to_line(lineno, self.margin)

    def current_position(self) -> None:
        line = f'line {self.file.y + 1}'
        col = f'col {self.file.x + 1}'
        line_count = max(len(self.file.lines) - 1, 1)
        lines_word = 'line' if line_count == 1 else 'lines'
        self.status.update(f'{line}, {col} (of {line_count} {lines_word})')

    def cut(self) -> None:
        if self.file.select_start:
            self.cut_buffer = self.file.cut_selection(self.margin)
            self.cut_selection = True
        else:
            self.cut_buffer = self.file.cut(self.cut_buffer)
            self.cut_selection = False

    def uncut(self) -> None:
        if self.cut_selection:
            self.file.uncut_selection(self.cut_buffer, self.margin)
        else:
            self.file.uncut(self.cut_buffer, self.margin)

    def _get_search_re(self, prompt: str) -> Union[Pattern[str], PromptResult]:
        response = self.prompt(prompt, history='search', default_prev=True)
        if response is PromptResult.CANCELLED:
            return response
        try:
            return re.compile(response)
        except re.error:
            self.status.update(f'invalid regex: {response!r}')
            return PromptResult.CANCELLED

    def _undo_redo(
            self,
            op: str,
            from_stack: List[Action],
            to_stack: List[Action],
    ) -> None:
        if not from_stack:
            self.status.update(f'nothing to {op}!')
        else:
            action = from_stack.pop()
            to_stack.append(action.apply(self.file))
            self.file.scroll_screen_if_needed(self.margin)
            self.status.update(f'{op}: {action.name}')

    def undo(self) -> None:
        self._undo_redo('undo', self.file.undo_stack, self.file.redo_stack)

    def redo(self) -> None:
        self._undo_redo('redo', self.file.redo_stack, self.file.undo_stack)

    def search(self) -> None:
        response = self._get_search_re('search')
        if response is not PromptResult.CANCELLED:
            self.file.search(response, self.status, self.margin)

    def replace(self) -> None:
        search_response = self._get_search_re('search (to replace)')
        if search_response is not PromptResult.CANCELLED:
            response = self.prompt(
                'replace with', history='replace', allow_empty=True,
            )
            if response is not PromptResult.CANCELLED:
                self.file.replace(self, search_response, response)

    def command(self) -> Optional[EditResult]:
        response = self.prompt('', history='command')
        if response == ':q':
            return EditResult.EXIT
        elif response == ':w':
            self.save()
        elif response == ':wq':
            self.save()
            return EditResult.EXIT
        elif response == ':sort':
            if self.file.select_start:
                self.file.sort_selection(self.margin)
            else:
                self.file.sort(self.margin)
            self.status.update('sorted!')
        elif response is not PromptResult.CANCELLED:
            self.status.update(f'invalid command: {response}')
        return None

    def save(self) -> Optional[PromptResult]:
        self.file.finalize_previous_action()

        # TODO: make directories if they don't exist
        # TODO: maybe use mtime / stat as a shortcut for hashing below
        # TODO: strip trailing whitespace?
        # TODO: save atomically?
        if self.file.filename is None:
            filename = self.prompt('enter filename')
            if filename is PromptResult.CANCELLED:
                return PromptResult.CANCELLED
            else:
                self.file.filename = filename

        if os.path.isfile(self.file.filename):
            with open(self.file.filename) as f:
                *_, sha256 = get_lines(f)
        else:
            sha256 = hashlib.sha256(b'').hexdigest()

        contents = self.file.nl.join(self.file.lines)
        sha256_to_save = hashlib.sha256(contents.encode()).hexdigest()

        # the file on disk is the same as when we opened it
        if sha256 not in (self.file.sha256, sha256_to_save):
            self.status.update('(file changed on disk, not implemented)')
            return PromptResult.CANCELLED

        with open(self.file.filename, 'w') as f:
            f.write(contents)

        self.file.modified = False
        self.file.sha256 = sha256_to_save
        num_lines = len(self.file.lines) - 1
        lines = 'lines' if num_lines != 1 else 'line'
        self.status.update(f'saved! ({num_lines} {lines} written)')

        # fix up modified state in undo / redo stacks
        for stack in (self.file.undo_stack, self.file.redo_stack):
            first = True
            for action in reversed(stack):
                action.end_modified = not first
                action.start_modified = True
                first = False
        return None

    def save_filename(self) -> Optional[PromptResult]:
        response = self.prompt('enter filename', default=self.file.filename)
        if response is PromptResult.CANCELLED:
            return PromptResult.CANCELLED
        else:
            self.file.filename = response
            return self.save()

    def quit_save_modified(self) -> Optional[EditResult]:
        if self.file.modified:
            response = self.quick_prompt(
                'file is modified - save [y(es), n(o)]?', 'yn',
            )
            if response == 'y':
                if self.save_filename() is not PromptResult.CANCELLED:
                    return EditResult.EXIT
                else:
                    return None
            elif response == 'n':
                return EditResult.EXIT
            else:
                assert response is PromptResult.CANCELLED
                return None
        return EditResult.EXIT

    def background(self) -> None:
        curses.endwin()
        os.kill(os.getpid(), signal.SIGSTOP)
        self.stdscr = _init_screen()
        self.resize()

    DISPATCH = {
        b'KEY_RESIZE': resize,
        b'^_': go_to_line,
        b'^C': current_position,
        b'^K': cut,
        b'^U': uncut,
        b'M-u': undo,
        b'M-U': redo,
        b'^W': search,
        b'^\\': replace,
        b'^[': command,
        b'^S': save,
        b'^O': save_filename,
        b'^X': quit_save_modified,
        b'kLFT3': lambda screen: EditResult.PREV,
        b'kRIT3': lambda screen: EditResult.NEXT,
        b'^Z': background,
    }


def _init_screen() -> 'curses._CursesWindow':
    # set the escape delay so curses does not pause waiting for sequences
    if sys.version_info >= (3, 9):  # pragma: no cover
        curses.set_escdelay(25)
    else:  # pragma: no cover
        os.environ.setdefault('ESCDELAY', '25')

    stdscr = curses.initscr()
    curses.noecho()
    curses.cbreak()
    # <enter> is not transformed into '\n' so it can be differentiated from ^J
    curses.nonl()
    # ^S / ^Q / ^Z / ^\ are passed through
    curses.raw()
    stdscr.keypad(True)
    with contextlib.suppress(curses.error):
        curses.start_color()
    # TODO: colors
    return stdscr


@contextlib.contextmanager
def make_stdscr() -> Generator['curses._CursesWindow', None, None]:
    """essentially `curses.wrapper` but split out to implement ^Z"""
    stdscr = _init_screen()
    try:
        yield stdscr
    finally:
        curses.endwin()
