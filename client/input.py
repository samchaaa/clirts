import sys

if sys.platform == "win32":
    import ctypes
    import msvcrt

    def _shift_down() -> bool:
        return bool(ctypes.windll.user32.GetKeyState(0x10) & 0x8000)

    def get_key() -> str | None:
        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ('\x00', '\xe0'):
                ch2 = msvcrt.getwch()
                # ctrl+arrows arrive as their own scan codes
                ctrl_mapping = {'\x8d': 'C-w', '\x91': 'C-s',
                                's': 'C-a', 't': 'C-d'}
                if ch2 in ctrl_mapping:
                    return ctrl_mapping[ch2]
                mapping = {'H': 'w', 'P': 's', 'K': 'a', 'M': 'd'}
                key = mapping.get(ch2)
                # msvcrt doesn't encode modifiers; uppercase = shift held
                if key and _shift_down():
                    return key.upper()
                return key
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
                        if ch3 == '1':
                            # modified arrow: ESC [ 1 ; <mod> <letter>
                            seq = sys.stdin.read(3)
                            if len(seq) == 3 and seq[0] == ';' and seq[2] in mapping:
                                key = mapping[seq[2]]
                                # mods 5-8 include ctrl (ctrl wins over shift)
                                if seq[1] in '5678':
                                    return 'C-' + key
                                # mods 2/4 include shift
                                return key.upper() if seq[1] in '24' else key
                            return None
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
