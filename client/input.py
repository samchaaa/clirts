import sys

if sys.platform == "win32":
    import msvcrt

    def get_key() -> str | None:
        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ('\x00', '\xe0'):
                ch2 = msvcrt.getwch()
                mapping = {'H': 'w', 'P': 's', 'K': 'a', 'M': 'd'}
                return mapping.get(ch2)
            return ch
        return None

else:
    import tty
    import termios
    import select

    _old_settings = None

    def _setup():
        global _old_settings
        _old_settings = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())

    def _restore():
        if _old_settings:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _old_settings)

    def get_key() -> str | None:
        r, _, _ = select.select([sys.stdin], [], [], 0)
        if r:
            ch = sys.stdin.read(1)
            if ch == '\x1b':
                r2, _, _ = select.select([sys.stdin], [], [], 0.01)
                if r2:
                    ch2 = sys.stdin.read(1)
                    if ch2 == '[':
                        ch3 = sys.stdin.read(1)
                        mapping = {'A': 'w', 'B': 's', 'D': 'a', 'C': 'd'}
                        return mapping.get(ch3)
                return '\x1b'
            return ch
        return None


def setup_terminal():
    if sys.platform != "win32":
        _setup()


def restore_terminal():
    if sys.platform != "win32":
        _restore()
