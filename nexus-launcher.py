#!/usr/bin/env python3
"""
NEXUS - Terminal Development Environment Manager
A cypherpunk-style terminal launcher for tmux projects and system tools.
"""

import os
import re
import sys
import json
import time
import select
import shlex
import tty
import termios
import subprocess
import importlib.util
from pathlib import Path
from datetime import datetime

# ═══════════════════════════════════════════════════════════════════════════════
# ANSI COLOR CODES
# ═══════════════════════════════════════════════════════════════════════════════

class Colors:
    # Reset
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    BLINK = '\033[5m'

    # Regular Colors
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'

    # Bright Colors
    BRIGHT_BLACK = '\033[90m'
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'

    # Background
    BG_BLACK = '\033[40m'
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_CYAN = '\033[46m'

C = Colors  # Alias for convenience

# ═══════════════════════════════════════════════════════════════════════════════
# ASCII ART & VISUAL ELEMENTS
# ═══════════════════════════════════════════════════════════════════════════════

NEXUS_LOGO = f"""
{C.CYAN}    ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗
    ████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝
    ██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗
    ██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║
    ██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║
    ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝{C.RESET}
"""

BOOT_SEQUENCE = f"""
{C.BRIGHT_BLACK}╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║  {C.GREEN}>>>{C.BRIGHT_BLACK} INITIALIZING NEXUS TERMINAL ENVIRONMENT {C.GREEN}<<<{C.BRIGHT_BLACK}                 ║
║                                                                  ║
║  {C.DIM}┌──────────────────────────────────────────────────────────┐{C.RESET}{C.BRIGHT_BLACK}    ║
║  {C.DIM}│{C.RESET} {C.CYAN}■{C.RESET} System Core ............ {C.GREEN}LOADED{C.RESET}                        {C.DIM}│{C.RESET}{C.BRIGHT_BLACK}    ║
║  {C.DIM}│{C.RESET} {C.CYAN}■{C.RESET} Project Database ........ {C.GREEN}SYNCED{C.RESET}                       {C.DIM}│{C.RESET}{C.BRIGHT_BLACK}    ║
║  {C.DIM}│{C.RESET} {C.CYAN}■{C.RESET} TMUX Interface .......... {C.GREEN}READY{C.RESET}                        {C.DIM}│{C.RESET}{C.BRIGHT_BLACK}    ║
║  {C.DIM}│{C.RESET} {C.CYAN}■{C.RESET} External Modules ........ {C.GREEN}ONLINE{C.RESET}                       {C.DIM}│{C.RESET}{C.BRIGHT_BLACK}    ║
║  {C.DIM}└──────────────────────────────────────────────────────────┘{C.RESET}{C.BRIGHT_BLACK}    ║
║                                                                  ║
║  {C.YELLOW}[ CYPHERPUNK DEVELOPMENT STATION v2.0 ]{C.BRIGHT_BLACK}                         ║
║  {C.DIM}"In code we trust"{C.RESET}{C.BRIGHT_BLACK}                                              ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝{C.RESET}
"""

MATRIX_CHARS = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ01"

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Get the directory where this script is located
SCRIPT_DIR = Path(__file__).parent.resolve()

# Private data directory (excluded from public git)
PRIVATE_DIR = SCRIPT_DIR / "nexus-private"

# Paths for user data
DATA_FILE = PRIVATE_DIR / "projects.json"
SNAPSHOTS_FILE = PRIVATE_DIR / "snapshots.json"
APPS_DIR = PRIVATE_DIR / "apps"
SCRIPTS_DIR = PRIVATE_DIR / "scripts"

# How many snapshots to preview inline on the main project list
SNAPSHOT_PREVIEW = 3

# Resume commands per agent. These use the personal zsh aliases:
#   claudep = CLAUDE_CONFIG_DIR=~/.claude-personal claude --dangerously-skip-permissions
#   codext  = codex --dangerously-bypass-hook-trust
RESUME_CMDS = {
    'claude': "claudep --resume {sid}",
    'claude-work': "claude --resume {sid}",
    'codex': "codext resume {sid}",
}

PERSONAL_CLAUDE_DIR = os.path.expanduser("~/.claude-personal")


def init_private_directory():
    """Create nexus-private directory structure if it doesn't exist."""
    if not PRIVATE_DIR.exists():
        PRIVATE_DIR.mkdir(parents=True)
        print(f"  {C.CYAN}Created:{C.RESET} {PRIVATE_DIR}/")

    for subdir in [APPS_DIR, SCRIPTS_DIR]:
        if not subdir.exists():
            subdir.mkdir(parents=True)
            print(f"  {C.CYAN}Created:{C.RESET} {subdir}/")

    # Create template files for new users
    apps_template = APPS_DIR / "_template.py.example"
    if not apps_template.exists():
        apps_template.write_text('''#!/usr/bin/env python3
"""
Template for NEXUS external programs.
Rename this file to your_app.py and implement main()
"""

def main():
    """Entry point for the application."""
    print("Hello from your NEXUS app!")
    # Your code here
    return 0  # Return 0 for success

if __name__ == "__main__":
    exit(main())
''')

    scripts_template = SCRIPTS_DIR / "_template.sh.example"
    if not scripts_template.exists():
        scripts_template.write_text('''#!/bin/bash
# Description: Template script for NEXUS
# Rename this file to your_script.sh

echo "Hello from your NEXUS script!"
# Your commands here
''')

    # Create README for nexus-private
    private_readme = PRIVATE_DIR / "README.md"
    if not private_readme.exists():
        private_readme.write_text('''# NEXUS Private Data

This directory contains your personal data for NEXUS launcher.
It is excluded from the public repository via .gitignore.

## Contents

- `apps/` - Your Python programs (must have a `main()` function)
- `scripts/` - Your shell scripts (.sh, .py, .bash)
- `projects.json` - Your tmux project configurations

## Backup Recommendation

Initialize a private git repository here to backup your personal data:

```bash
cd nexus-private
git init
git add .
git commit -m "Initial backup"
git remote add origin git@github.com:YOUR_USER/nexus-private.git
git push -u origin main
```
''')

# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def clear_screen():
    os.system('clear' if os.name != 'nt' else 'cls')

def print_header(title, width=60):
    """Print a styled header box."""
    print(f"\n{C.CYAN}{'═' * width}{C.RESET}")
    padding = (width - len(title) - 4) // 2
    print(f"{C.CYAN}║{C.RESET}{' ' * padding}{C.BOLD}{C.BRIGHT_CYAN}{title}{C.RESET}{' ' * (width - padding - len(title) - 2)}{C.CYAN}║{C.RESET}")
    print(f"{C.CYAN}{'═' * width}{C.RESET}")

def print_divider(char='─', width=60):
    print(f"{C.BRIGHT_BLACK}{char * width}{C.RESET}")

def print_status(message, status="OK", status_color=None):
    """Print a status line with aligned status indicator."""
    if status_color is None:
        status_color = C.GREEN if status == "OK" else C.RED if status == "ERR" else C.YELLOW
    dots = '.' * (50 - len(message))
    print(f"  {C.CYAN}■{C.RESET} {message} {C.DIM}{dots}{C.RESET} {status_color}{status}{C.RESET}")

def animated_text(text, delay=0.02):
    """Print text with typing animation."""
    for char in text:
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(delay)
    print()

def loading_bar(duration=1.5, width=40):
    """Display an animated loading bar."""
    for i in range(width + 1):
        progress = int((i / width) * 100)
        bar = f"{C.GREEN}{'█' * i}{C.BRIGHT_BLACK}{'░' * (width - i)}{C.RESET}"
        sys.stdout.write(f"\r  [{bar}] {progress}%")
        sys.stdout.flush()
        time.sleep(duration / width)
    print()

def prompt(text, default=None):
    """Styled input prompt."""
    if default:
        result = input(f"  {C.YELLOW}>{C.RESET} {text} {C.DIM}[{default}]{C.RESET}: ").strip()
        return result if result else default
    return input(f"  {C.YELLOW}>{C.RESET} {text}: ").strip()

def confirm(text, default=True):
    """Yes/No confirmation prompt."""
    suffix = "[S/n]" if default else "[s/N]"
    response = input(f"  {C.YELLOW}?{C.RESET} {text} {C.DIM}{suffix}{C.RESET}: ").strip().lower()
    if not response:
        return default
    return response in ('s', 'si', 'y', 'yes')

def show_error(message):
    print(f"\n  {C.RED}✖ ERROR:{C.RESET} {message}")

def show_success(message):
    print(f"\n  {C.GREEN}✔{C.RESET} {message}")

def show_warning(message):
    print(f"\n  {C.YELLOW}⚠{C.RESET} {message}")

def show_info(message):
    print(f"  {C.CYAN}ℹ{C.RESET} {message}")

def animated_menu_prompt(prompt_text):
    """Styled input prompt with intense cyberpunk animation."""
    # Multi-layer spinners for intense effect (fixed width so text doesn't shift)
    _spinner_main = ['█▄', '▄█', '█▀', '▀█', '●▮', '▮●', '◆◇', '◇◆']
    _spinner_side = ['◄  ', '◄◄ ', '◄◄◄', '◄◄ ', '◄  ', '►  ', '►► ', '►►►', '►► ', '►  ']
    _statuses = [
        ('NEXUS ACTIVE      ', C.BRIGHT_CYAN),
        ('SYS_LOCKED       ', C.BRIGHT_MAGENTA),
        ('FIREWALL UP      ', C.BRIGHT_GREEN),
        ('NEURAL_NET ON    ', C.BRIGHT_YELLOW),
        ('CRYPTO_ENGAGED   ', C.BRIGHT_CYAN),
        ('DAEMON_RUNNING   ', C.BRIGHT_RED),
        ('STREAM_ONLINE    ', C.BRIGHT_MAGENTA),
        ('NODE_CONNECTED   ', C.BRIGHT_GREEN),
        ('SIGNAL_STRONG    ', C.BRIGHT_YELLOW),
        ('PROTOCOL_OK      ', C.BRIGHT_CYAN),
    ]

    sys.stdout.write(f"  {C.BRIGHT_CYAN}◆{C.RESET} {C.DIM}NEXUS ACTIVE{C.RESET}\n")
    sys.stdout.write(f"  {C.BRIGHT_MAGENTA}>{C.RESET} {prompt_text}: ")
    sys.stdout.flush()

    if not sys.stdin.isatty():
        return input("").strip()

    result = ""
    frame  = 0
    fd     = sys.stdin.fileno()
    old    = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            r, _, _ = select.select([sys.stdin], [], [], 0.15)
            if r:
                ch = sys.stdin.read(1)
                if ch in ('\r', '\n'):
                    sys.stdout.write('\n')
                    sys.stdout.flush()
                    break
                elif ch in ('\x7f', '\x08'):
                    if result:
                        result = result[:-1]
                        sys.stdout.write('\b \b')
                        sys.stdout.flush()
                elif ch == '\x03':
                    raise KeyboardInterrupt
                elif ch == '\x1b':
                    while select.select([sys.stdin], [], [], 0)[0]:
                        sys.stdin.read(1)
                elif ch >= ' ':
                    result += ch
                    sys.stdout.write(ch)
                    sys.stdout.flush()

            # Animated status with color cycling
            sp_main = _spinner_main[frame % len(_spinner_main)]
            sp_side_l = _spinner_side[frame % len(_spinner_side)]
            sp_side_r = _spinner_side[-(frame % len(_spinner_side))-1]

            status_idx = (frame // 3) % len(_statuses)
            status_text, status_color = _statuses[status_idx]

            # Glitch effect occasionally
            if frame % 12 == 0:
                status_text = status_text.replace(' ', '█')

            sys.stdout.write(f"\033[s\033[A\r  {sp_side_l}{C.RESET} {status_color}{sp_main} {status_text}{C.RESET} {sp_side_r}\033[u")
            sys.stdout.flush()
            frame += 1
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    return result.strip()

# ═══════════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class PaneConfig:
    """Configuration for a single tmux pane."""
    def __init__(self, name, use_cd=True, use_venv=True, custom_cmd=None):
        self.name = name
        self.use_cd = use_cd
        self.use_venv = use_venv
        self.custom_cmd = custom_cmd

    def to_dict(self):
        return self.__dict__

    @classmethod
    def from_dict(cls, data):
        return cls(**data)


class Project:
    """
    Project configuration including layout and pane settings.
    """
    def __init__(self, alias, path, venv_path, activation_cmd=None,
                 layout_splits=None, panes_config=None):
        self.alias = alias
        self.path = path
        self.venv_path = venv_path
        self.activation_cmd = activation_cmd if activation_cmd else f"source {os.path.join(venv_path, 'bin', 'activate')}"

        if layout_splits is None:
            self.layout_splits = [
                {'direction': 'h', 'percent': 30, 'target': None},
                {'direction': 'v', 'percent': 30, 'target': '0'}
            ]
        else:
            self.layout_splits = layout_splits

        if panes_config is None:
            self.panes_config = [
                PaneConfig("Editor/Main", True, True, None),
                PaneConfig("Server", True, True, None),
                PaneConfig("Extra", True, True, None)
            ]
        else:
            self.panes_config = [PaneConfig.from_dict(p) if isinstance(p, dict) else p for p in panes_config]

    def to_dict(self):
        data = self.__dict__.copy()
        data['panes_config'] = [p.to_dict() for p in self.panes_config]
        return data

    @classmethod
    def from_dict(cls, data):
        return cls(**data)


# ═══════════════════════════════════════════════════════════════════════════════
# PROJECT MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class ProjectManager:
    def __init__(self, filepath):
        self.filepath = filepath
        self.projects = self._load_projects()
        self.global_config = {
            'default_h_split': 30,
            'default_v_split': 30
        }

    def _load_projects(self):
        if not os.path.exists(self.filepath):
            return []
        try:
            with open(self.filepath, 'r') as f:
                data = json.load(f)
                return [Project.from_dict(p) for p in data]
        except (json.JSONDecodeError, IOError):
            return []

    def save_projects(self):
        with open(self.filepath, 'w') as f:
            json.dump([p.to_dict() for p in self.projects], f, indent=4)

    def add_project(self, project):
        self.projects.append(project)
        self.save_projects()

    def delete_project(self, identifier):
        project = self.get_project(identifier)
        if project:
            self.projects.remove(project)
            self.save_projects()
            return True
        return False

    def get_project(self, identifier):
        if str(identifier).isdigit():
            idx = int(identifier) - 1
            if 0 <= idx < len(self.projects):
                return self.projects[idx]
        for p in self.projects:
            if p.alias.lower() == str(identifier).lower():
                return p
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION SNAPSHOTS
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_path(path):
    """Normalize a project/pane path for comparison (~, escapes, trailing /)."""
    if not path:
        return ""
    return os.path.normpath(os.path.expanduser(path.replace('\\', '')))


class Snapshot:
    """
    A saved agent session tied to a project.

    Restoring one relaunches the project's normal tmux layout and additionally
    resumes the Claude/Codex conversation in the main (largest) pane.
    """
    def __init__(self, id, project, tool, session_id, description="",
                 created_at=None, window_name=None, config_dir=None, batch=None):
        self.id = id
        self.project = project
        self.tool = tool                # 'claude' | 'codex'
        self.session_id = session_id
        self.description = description
        self.created_at = created_at or datetime.now().isoformat(timespec='seconds')
        self.window_name = window_name
        self.config_dir = config_dir    # claude only: which install owns the session
        self.batch = batch              # capture run this snapshot belongs to

    @property
    def date_label(self):
        try:
            return datetime.fromisoformat(self.created_at).strftime("%m-%d")
        except ValueError:
            return "??-??"

    @property
    def stamp(self):
        try:
            return datetime.fromisoformat(self.created_at).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return self.created_at

    def resume_cmd(self):
        """The shell command that reattaches this session."""
        key = self.tool
        if self.tool == 'claude' and normalize_path(self.config_dir or "") \
                and normalize_path(self.config_dir) != normalize_path(PERSONAL_CLAUDE_DIR):
            key = 'claude-work'
        template = RESUME_CMDS.get(key)
        if not template:
            return None
        return template.format(sid=self.session_id)

    def to_dict(self):
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data):
        return cls(**data)


class SnapshotManager:
    def __init__(self, filepath):
        self.filepath = filepath
        self.snapshots = self._load()

    def _load(self):
        if not os.path.exists(self.filepath):
            return []
        try:
            with open(self.filepath, 'r') as f:
                return [Snapshot.from_dict(s) for s in json.load(f)]
        except (json.JSONDecodeError, IOError, TypeError):
            return []

    def save(self):
        with open(self.filepath, 'w') as f:
            json.dump([s.to_dict() for s in self.snapshots], f, indent=4)

    def next_id(self):
        return max((s.id for s in self.snapshots), default=0) + 1

    def add(self, snapshot):
        self.snapshots.append(snapshot)
        self.save()

    def delete(self, snap_id):
        before = len(self.snapshots)
        self.snapshots = [s for s in self.snapshots if s.id != snap_id]
        if len(self.snapshots) != before:
            self.save()
            return True
        return False

    def get(self, snap_id):
        for s in self.snapshots:
            if str(s.id) == str(snap_id):
                return s
        return None

    def for_project(self, alias):
        """All snapshots of a project, newest first."""
        return sorted((s for s in self.snapshots if s.project == alias),
                      key=lambda s: s.created_at, reverse=True)

    def batches(self):
        """Capture batches, newest first: [(batch_stamp, [snapshots...]), ...]"""
        groups = {}
        for s in self.snapshots:
            groups.setdefault(s.batch or s.created_at, []).append(s)
        return sorted(groups.items(), key=lambda kv: kv[0], reverse=True)


class SessionDetector:
    """
    Finds live Claude/Codex sessions running inside tmux panes.

    Both agents publish their session id somewhere reliable, so nothing here
    guesses:
      - Claude writes  <CLAUDE_CONFIG_DIR>/sessions/<pid>.json  with sessionId
      - Codex keeps its rollout transcript open as a file descriptor
    """
    AGENTS = ('claude', 'codex')
    ROLLOUT_RE = re.compile(r'rollout-[\dT:-]+-([0-9a-fA-F-]{36})\.jsonl')

    def _process_tree(self):
        out = subprocess.run(['ps', '-eo', 'pid=,ppid=,comm='],
                             capture_output=True, text=True).stdout
        children, comm = {}, {}
        for line in out.splitlines():
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            try:
                pid, ppid = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            children.setdefault(ppid, []).append(pid)
            comm[pid] = parts[2].strip()
        return children, comm

    def _find_agent_pid(self, root_pid, children, comm):
        """Walk down from a pane's shell until an agent process shows up."""
        stack, seen = [root_pid], set()
        while stack:
            pid = stack.pop()
            if pid in seen:
                continue
            seen.add(pid)
            if comm.get(pid) in self.AGENTS:
                return pid, comm[pid]
            stack.extend(children.get(pid, []))
        return None, None

    def _read_environ(self, pid):
        try:
            with open(f'/proc/{pid}/environ', 'rb') as f:
                raw = f.read().decode('utf-8', 'replace')
        except OSError:
            return {}
        env = {}
        for item in raw.split('\0'):
            if '=' in item:
                k, v = item.split('=', 1)
                env[k] = v
        return env

    def _claude_session(self, pid):
        """Read the session id Claude registered for this pid."""
        cfg = self._read_environ(pid).get('CLAUDE_CONFIG_DIR') \
            or os.path.expanduser('~/.claude')
        registry = Path(cfg) / 'sessions' / f'{pid}.json'
        try:
            data = json.loads(registry.read_text())
            return data.get('sessionId'), cfg
        except (OSError, json.JSONDecodeError):
            return None, cfg

    def _codex_session(self, pid):
        """Pull the session uuid out of the rollout file Codex holds open."""
        try:
            fds = os.listdir(f'/proc/{pid}/fd')
        except OSError:
            return None
        for fd in fds:
            try:
                target = os.readlink(f'/proc/{pid}/fd/{fd}')
            except OSError:
                continue
            match = self.ROLLOUT_RE.search(target)
            if match:
                return match.group(1)
        return None

    def scan(self):
        """Return one entry per tmux pane that is running an agent session."""
        fmt = ("#{session_name}\t#{window_index}\t#{window_name}\t#{pane_index}"
               "\t#{pane_pid}\t#{pane_current_path}")
        out = subprocess.run(['tmux', 'list-panes', '-a', '-F', fmt],
                             capture_output=True, text=True)
        if out.returncode != 0:
            return []

        children, comm = self._process_tree()
        found = []
        for line in out.stdout.splitlines():
            parts = line.split('\t')
            if len(parts) < 6:
                continue
            sess, win_idx, win_name, pane_idx, pane_pid, path = parts
            try:
                agent_pid, tool = self._find_agent_pid(int(pane_pid), children, comm)
            except ValueError:
                continue
            if not agent_pid:
                continue

            config_dir = None
            if tool == 'claude':
                session_id, config_dir = self._claude_session(agent_pid)
            else:
                session_id = self._codex_session(agent_pid)
            if not session_id:
                continue

            found.append({
                'target': f"{sess}:{win_idx}.{pane_idx}",
                'window': win_name,
                'path': path,
                'tool': tool,
                'session_id': session_id,
                'config_dir': config_dir,
            })
        return found


def draft_description(pane_target, config_dir=None, lines=150):
    """Ask Claude to summarize what a pane was doing, for a snapshot label."""
    cap = subprocess.run(['tmux', 'capture-pane', '-t', pane_target, '-p',
                          '-S', f'-{lines}'], capture_output=True, text=True)
    if cap.returncode != 0 or not cap.stdout.strip():
        return None

    ask = (
        "Este es el contenido reciente de una terminal donde se trabajaba con "
        "un agente de IA sobre un proyecto de software. Resume en UNA sola "
        "linea, en espanol, maximo 110 caracteres, en que se estaba trabajando "
        "y que quedo pendiente. Responde solo con esa linea, sin comillas ni "
        "prefijos.\n\n---\n" + cap.stdout
    )

    env = dict(os.environ)
    env['CLAUDE_CONFIG_DIR'] = config_dir or PERSONAL_CLAUDE_DIR
    try:
        res = subprocess.run(['claude', '-p', '--model', 'haiku', ask],
                             capture_output=True, text=True, env=env, timeout=180)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if res.returncode != 0:
        return None
    return res.stdout.strip().splitlines()[0].strip() if res.stdout.strip() else None


# ═══════════════════════════════════════════════════════════════════════════════
# TMUX ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

class TmuxOrchestrator:
    def check_tmux_session(self):
        if not os.environ.get('TMUX'):
            show_error("Este script debe ejecutarse DENTRO de una sesion de tmux")
            return False
        return True

    def _build_cmds(self, project, pane_conf):
        """Build command list for a pane based on its config."""
        cmds = []
        if pane_conf.use_cd:
            cmds.append(f"cd {shlex.quote(os.path.expanduser(project.path))}")
        if pane_conf.use_venv:
            cmds.append(project.activation_cmd)

        cmds.append("clear")

        if pane_conf.custom_cmd:
            cmds.append(pane_conf.custom_cmd)
        return cmds

    def _send_keys(self, pane_target, commands):
        for cmd in commands:
            subprocess.run(['tmux', 'send-keys', '-t', str(pane_target), cmd, 'C-m'],
                         capture_output=True)

    def _tmux_id(self, args):
        res = subprocess.run(['tmux'] + args, capture_output=True, text=True)
        return res.stdout.strip() if res.returncode == 0 else None

    def launch_layout(self, project, resume_cmd=None, new_window=False,
                      window_name=None):
        """
        Build the project's pane layout and run each pane's configured command.

        resume_cmd  extra command for the main (largest) pane, used to reattach
                    a Claude/Codex session on top of the normal layout.
        new_window  build in a fresh tmux window instead of the current one, so
                    several projects can be restored in one go.
        """
        if not self.check_tmux_session():
            return False

        show_info(f"Launching layout for {C.CYAN}{project.alias}{C.RESET}...")

        if new_window:
            win = self._tmux_id(['new-window', '-P', '-F', '#{window_id}'])
        else:
            win = self._tmux_id(['display-message', '-p', '#{window_id}'])
        if not win:
            show_error("Could not resolve the target tmux window")
            return False

        # Execute splits
        for split in project.layout_splits:
            target = split.get('target') or '0'
            subprocess.run(['tmux', 'split-window', f"-{split['direction']}",
                            '-p', str(split['percent']), '-t', f"{win}.{target}"],
                           capture_output=True)

        # Pane index -> panes_config index.
        # 3-pane layout: pane 0 = main (the big one, top-left), pane 1 = the
        # short bottom-left strip, pane 2 = the tall right column where the
        # server runs. 2-pane layout maps straight through.
        n = len(project.panes_config)
        mapping = [(0, 0), (1, 1)] if n == 2 else [(0, 0), (1, 2), (2, 1)]

        for pane_idx, conf_idx in mapping:
            if conf_idx >= n:
                continue
            cmds = self._build_cmds(project, project.panes_config[conf_idx])
            if pane_idx == 0 and resume_cmd:
                cmds.append(resume_cmd)
            self._send_keys(f"{win}.{pane_idx}", cmds)

        # Rename window and set focus on the main pane
        subprocess.run(['tmux', 'rename-window', '-t', win,
                        window_name or project.alias], capture_output=True)
        subprocess.run(['tmux', 'select-pane', '-t', f"{win}.0"], capture_output=True)

        show_success(f"Project '{project.alias}' loaded successfully")
        return win


# ═══════════════════════════════════════════════════════════════════════════════
# EXTERNAL PROGRAMS MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class ExternalProgramsManager:
    """Manages external Python programs in the apps directory."""

    def __init__(self, apps_dir=APPS_DIR):
        self.apps_dir = Path(apps_dir)

    def list_programs(self):
        """List all available Python programs."""
        programs = []
        if self.apps_dir.exists():
            for f in sorted(self.apps_dir.glob("*.py")):
                if not f.name.startswith('_'):
                    programs.append({
                        'name': f.stem,
                        'path': str(f),
                        'description': self._get_description(f)
                    })
        return programs

    def _get_description(self, filepath):
        """Extract docstring from Python file."""
        try:
            with open(filepath, 'r') as f:
                content = f.read()
                # Simple docstring extraction
                if '"""' in content:
                    start = content.find('"""') + 3
                    end = content.find('"""', start)
                    if end > start:
                        doc = content[start:end].strip().split('\n')[0]
                        return doc[:50] + '...' if len(doc) > 50 else doc
        except:
            pass
        return "No description"

    def run_program(self, identifier):
        """Run an external Python program."""
        programs = self.list_programs()

        # Find by index or name
        program = None
        if str(identifier).isdigit():
            idx = int(identifier) - 1
            if 0 <= idx < len(programs):
                program = programs[idx]
        else:
            for p in programs:
                if p['name'].lower() == str(identifier).lower():
                    program = p
                    break

        if not program:
            show_error(f"Program '{identifier}' not found")
            return False

        print_header(f"RUNNING: {program['name'].upper()}")
        print()

        try:
            # Method 1: Import and run main()
            spec = importlib.util.spec_from_file_location(program['name'], program['path'])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if hasattr(module, 'main'):
                result = module.main()
                return result == 0 if isinstance(result, int) else True
            else:
                show_warning("Program has no main() function - executed as script")
                return True

        except Exception as e:
            show_error(f"Execution failed: {str(e)}")
            return False


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM SCRIPTS MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class SystemScriptsManager:
    """Manages system configuration scripts."""

    def __init__(self, scripts_dir=SCRIPTS_DIR):
        self.scripts_dir = Path(scripts_dir)

    def list_scripts(self):
        """List all available scripts."""
        scripts = []
        if self.scripts_dir.exists():
            for ext in ['*.sh', '*.py', '*.bash']:
                for f in sorted(self.scripts_dir.glob(ext)):
                    if not f.name.startswith('_'):
                        scripts.append({
                            'name': f.stem,
                            'path': str(f),
                            'type': f.suffix,
                            'description': self._get_description(f)
                        })
        return scripts

    def _get_description(self, filepath):
        """Extract description from script comments."""
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    if line.startswith('# Description:'):
                        return line.replace('# Description:', '').strip()[:50]
        except:
            pass
        return "No description"

    def run_script(self, identifier, sudo=False):
        """Run a system script."""
        scripts = self.list_scripts()

        script = None
        if str(identifier).isdigit():
            idx = int(identifier) - 1
            if 0 <= idx < len(scripts):
                script = scripts[idx]
        else:
            for s in scripts:
                if s['name'].lower() == str(identifier).lower():
                    script = s
                    break

        if not script:
            show_error(f"Script '{identifier}' not found")
            return False

        print_header(f"EXECUTING: {script['name']}")
        print()

        try:
            cmd = []
            if sudo:
                cmd.append('sudo')

            if script['type'] == '.py':
                cmd.extend(['python3', script['path']])
            else:
                cmd.extend(['bash', script['path']])

            result = subprocess.run(cmd)
            return result.returncode == 0

        except Exception as e:
            show_error(f"Execution failed: {str(e)}")
            return False


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════════════════════

class NexusApp:
    def __init__(self):
        self.manager = ProjectManager(DATA_FILE)
        self.snapshots = SnapshotManager(SNAPSHOTS_FILE)
        self.detector = SessionDetector()
        self.tmux = TmuxOrchestrator()
        self.programs = ExternalProgramsManager()
        self.scripts = SystemScriptsManager()
        self.show_boot = True

    # ─────────────────────────────────────────────────────────────────────────
    # BOOT SEQUENCE
    # ─────────────────────────────────────────────────────────────────────────

    def boot_sequence(self):
        """Display the boot animation."""
        clear_screen()
        print(NEXUS_LOGO)
        time.sleep(0.1)

        # Animated loading
        print(f"\n  {C.BRIGHT_BLACK}Initializing system...{C.RESET}")
        loading_bar(duration=0.5)

        clear_screen()
        print(NEXUS_LOGO)
        print(BOOT_SEQUENCE)

        time.sleep(0.1)
        input(f"\n  {C.DIM}Press ENTER to continue...{C.RESET}")

    # ─────────────────────────────────────────────────────────────────────────
    # PROJECT MANAGEMENT UI
    # ─────────────────────────────────────────────────────────────────────────

    def create_project_ui(self):
        """Interactive project creation wizard."""
        print_header("NEW PROJECT")

        alias = prompt("Project alias")
        if not alias:
            show_error("Alias is required")
            return

        path = prompt("Absolute project path")
        venv = prompt("Absolute venv path (empty = no venv)", "")

        if venv:
            default_activate = f"source {os.path.join(venv, 'bin', 'activate')}"
        else:
            default_activate = "echo ."
        act_cmd = prompt("Venv activation command", default_activate)

        print(f"\n  {C.CYAN}─── Panel Configuration ───{C.RESET}")
        h_size = self.manager.global_config['default_h_split']
        v_size = self.manager.global_config['default_v_split']

        three_panes = confirm("Use 3 panels? (No = 2 panels)", default=True)

        # Main panel (always)
        print(f"\n  {C.YELLOW}[Left / Main Panel]{C.RESET}")
        main_cmd  = prompt("Command to execute (optional)", "")
        main_cd   = confirm("Enter project directory?")
        main_venv = confirm("Activate virtual environment?")

        # Right panel (always)
        print(f"\n  {C.YELLOW}[Right Panel]{C.RESET} {C.DIM}({h_size}% width){C.RESET}")
        srv_cmd  = prompt("Command to execute (optional)", "")
        srv_cd   = confirm("Enter project directory?")
        srv_venv = confirm("Activate virtual environment?")

        if three_panes:
            # Bottom-left panel (optional)
            print(f"\n  {C.YELLOW}[Bottom-Left Panel]{C.RESET} {C.DIM}({v_size}% height){C.RESET}")
            ext_cmd  = prompt("Command to execute (optional)", "")
            ext_cd   = confirm("Enter project directory?")
            ext_venv = confirm("Activate virtual environment?")

            layout = [
                {'direction': 'h', 'percent': h_size, 'target': None},
                {'direction': 'v', 'percent': v_size, 'target': '0'}
            ]
            panes = [
                PaneConfig("Main",   main_cd,  main_venv,  main_cmd  or None),
                PaneConfig("Server", srv_cd,   srv_venv,   srv_cmd   or None),
                PaneConfig("Extra",  ext_cd,   ext_venv,   ext_cmd   or None),
            ]
        else:
            layout = [
                {'direction': 'h', 'percent': h_size, 'target': None}
            ]
            panes = [
                PaneConfig("Main",   main_cd,  main_venv,  main_cmd  or None),
                PaneConfig("Server", srv_cd,   srv_venv,   srv_cmd   or None),
            ]

        new_proj = Project(alias, path, venv, act_cmd, layout, panes)
        self.manager.add_project(new_proj)
        show_success(f"Project '{alias}' created successfully")

    def delete_project_ui(self):
        """Delete a project."""
        if not self.manager.projects:
            show_warning("No projects to delete")
            return

        print_header("DELETE PROJECT")
        self._display_projects_list()

        choice = prompt("Enter project number or alias to delete")
        project = self.manager.get_project(choice)

        if project:
            if confirm(f"Delete project '{project.alias}'?", default=False):
                self.manager.delete_project(choice)
                show_success(f"Project '{project.alias}' deleted")
        else:
            show_error("Project not found")

    def edit_project_ui(self):
        """Edit an existing project's pane configurations."""
        if not self.manager.projects:
            show_warning("No projects to edit")
            return

        print_header("EDIT PROJECT")
        self._display_projects_list()

        choice = prompt("Enter project number or alias to edit")
        project = self.manager.get_project(choice)

        if not project:
            show_error("Project not found")
            return

        while True:
            clear_screen()
            print(NEXUS_LOGO)
            print_header(f"EDITING: {project.alias}")

            # Show current config
            print(f"\n  {C.CYAN}General{C.RESET}")
            print(f"  {C.BRIGHT_BLACK}{'─' * 50}{C.RESET}")
            print(f"  {C.YELLOW}1{C.RESET} │ Alias          : {C.GREEN}{project.alias}{C.RESET}")
            print(f"  {C.YELLOW}2{C.RESET} │ Path           : {C.GREEN}{project.path}{C.RESET}")
            print(f"  {C.YELLOW}3{C.RESET} │ Venv path      : {C.GREEN}{project.venv_path}{C.RESET}")
            print(f"  {C.YELLOW}4{C.RESET} │ Activation cmd : {C.GREEN}{project.activation_cmd}{C.RESET}")

            pane_count = len(project.panes_config)
            print(f"\n  {C.CYAN}Panes{C.RESET} {C.DIM}({pane_count} total){C.RESET}")
            print(f"  {C.BRIGHT_BLACK}{'─' * 50}{C.RESET}")
            for i, pane in enumerate(project.panes_config):
                cd_status = f"{C.GREEN}Yes{C.RESET}" if pane.use_cd else f"{C.RED}No{C.RESET}"
                venv_status = f"{C.GREEN}Yes{C.RESET}" if pane.use_venv else f"{C.RED}No{C.RESET}"
                cmd_display = pane.custom_cmd if pane.custom_cmd else f"{C.DIM}(none){C.RESET}"
                label = chr(ord('A') + i)
                print(f"  {C.YELLOW}[{label}]{C.RESET} │ {C.BOLD}{pane.name:<10}{C.RESET} cd:{cd_status}  venv:{venv_status}  cmd: {cmd_display}")

            if pane_count < 3:
                print(f"  {C.YELLOW}[+]{C.RESET} │ Add panel (current: {pane_count} → 3)")
            if pane_count > 2:
                print(f"  {C.YELLOW}[-]{C.RESET} │ Remove last panel (current: {pane_count} → 2)")

            print(f"\n  {C.YELLOW}[X]{C.RESET} │ Back (save & exit)")
            print()

            opt = prompt("Option").strip()

            if opt.lower() == 'x':
                self.manager.save_projects()
                show_success(f"Project '{project.alias}' saved")
                break

            elif opt == '1':
                new_val = prompt("New alias", project.alias)
                if new_val:
                    project.alias = new_val

            elif opt == '2':
                new_val = prompt("New path", project.path)
                if new_val:
                    project.path = new_val

            elif opt == '3':
                new_val = prompt("New venv path", project.venv_path)
                project.venv_path = new_val

            elif opt == '4':
                new_val = prompt("New activation command", project.activation_cmd)
                if new_val:
                    project.activation_cmd = new_val

            elif opt == '+' and len(project.panes_config) < 3:
                h_size = self.manager.global_config['default_h_split']
                v_size = self.manager.global_config['default_v_split']
                print(f"\n  {C.YELLOW}[New Bottom-Left Panel]{C.RESET}")
                ext_cmd  = prompt("Command to execute (optional)", "")
                ext_cd   = confirm("Enter project directory?")
                ext_venv = confirm("Activate virtual environment?")
                project.panes_config.append(
                    PaneConfig("Extra", ext_cd, ext_venv, ext_cmd or None)
                )
                project.layout_splits.append(
                    {'direction': 'v', 'percent': v_size, 'target': '0'}
                )
                show_success("Panel added")
                time.sleep(0.5)

            elif opt == '-' and len(project.panes_config) > 2:
                if confirm(f"Remove last panel '{project.panes_config[-1].name}'?", default=False):
                    project.panes_config.pop()
                    project.layout_splits = [s for s in project.layout_splits
                                             if s['direction'] != 'v']
                    show_success("Panel removed")
                    time.sleep(0.5)

            elif opt.upper() in [chr(ord('A') + i) for i in range(len(project.panes_config))]:
                pane_idx = ord(opt.upper()) - ord('A')
                pane = project.panes_config[pane_idx]
                self._edit_pane(pane)

            else:
                show_error("Invalid option")
                time.sleep(0.5)

    def _edit_pane(self, pane):
        """Edit a single pane configuration."""
        print(f"\n  {C.CYAN}─── Editing pane: {pane.name} ───{C.RESET}")

        new_name = prompt("Pane name", pane.name)
        if new_name:
            pane.name = new_name

        pane.use_cd = confirm("Enter project directory?", pane.use_cd)
        pane.use_venv = confirm("Activate virtual environment?", pane.use_venv)

        if pane.custom_cmd:
            print(f"  {C.DIM}Current command: {pane.custom_cmd}{C.RESET}")
        new_cmd = prompt("Command to execute (empty to clear)")
        if new_cmd:
            pane.custom_cmd = new_cmd
        else:
            pane.custom_cmd = None

        show_success(f"Pane '{pane.name}' updated")
        time.sleep(0.5)

    def _display_projects_list(self, with_snapshots=False):
        """Display formatted project list, optionally with snapshot previews."""
        if not self.manager.projects:
            print(f"\n  {C.DIM}No projects configured{C.RESET}")
            return False

        print()
        for i, p in enumerate(self.manager.projects, 1):
            path_display = p.path if len(p.path) < 40 else '...' + p.path[-37:]
            print(f"  {C.CYAN}{i:>2}{C.RESET} │ {C.BOLD}{p.alias:<15}{C.RESET} {C.DIM}{path_display}{C.RESET}")

            if not with_snapshots:
                continue

            snaps = self.snapshots.for_project(p.alias)
            if not snaps:
                continue

            chips = "  ".join(
                f"{C.MAGENTA}#{s.id}{C.RESET}{C.DIM}·{s.date_label}{C.RESET}"
                for s in snaps[:SNAPSHOT_PREVIEW]
            )
            more = ""
            if len(snaps) > SNAPSHOT_PREVIEW:
                more = f"  {C.DIM}(+{len(snaps) - SNAPSHOT_PREVIEW}){C.RESET}"
            print(f"       {C.BRIGHT_BLACK}⌁{C.RESET} {C.DIM}s{i}{C.RESET} {chips}{more}")
        print()
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # SNAPSHOTS UI
    # ─────────────────────────────────────────────────────────────────────────

    def _tool_tag(self, tool):
        color = C.BRIGHT_MAGENTA if tool == 'claude' else C.BRIGHT_GREEN
        return f"{color}{tool:<6}{C.RESET}"

    def restore_snapshot(self, snapshot, new_window=False):
        """Relaunch a project's layout and resume its agent session."""
        project = self.manager.get_project(snapshot.project)
        if not project:
            show_error(f"Project '{snapshot.project}' no longer exists")
            return False

        resume = snapshot.resume_cmd()
        if not resume:
            show_error(f"Unknown tool '{snapshot.tool}' — cannot build resume command")
            return False

        show_info(f"Resuming {self._tool_tag(snapshot.tool)}{C.DIM}{snapshot.session_id}{C.RESET}")
        return self.tmux.launch_layout(project, resume_cmd=resume,
                                       new_window=new_window,
                                       window_name=snapshot.window_name)

    def project_snapshots_menu(self, project):
        """Full snapshot history for one project, with restore/delete."""
        while True:
            clear_screen()
            print(NEXUS_LOGO)
            print_header(f"SNAPSHOTS: {project.alias}")

            snaps = self.snapshots.for_project(project.alias)
            if not snaps:
                print(f"\n  {C.DIM}No snapshots saved for this project{C.RESET}")
                print(f"  {C.DIM}Use [K] on the main menu to capture live sessions{C.RESET}\n")
            else:
                print()
                for s in snaps:
                    desc = s.description or f"{C.DIM}(no description){C.RESET}"
                    print(f"  {C.MAGENTA}{s.id:>3}{C.RESET} │ {self._tool_tag(s.tool)} │ "
                          f"{C.DIM}{s.stamp}{C.RESET}")
                    print(f"      {C.BRIGHT_BLACK}└{C.RESET} {desc}")
                print()

            print_divider()
            print(f"  {C.YELLOW}[#]{C.RESET} Restore snapshot   "
                  f"{C.YELLOW}[D]{C.RESET} Delete   {C.YELLOW}[B]{C.RESET} Back")
            print_divider()

            choice = prompt("Select snapshot or option")
            if not choice or choice.lower() == 'b':
                return False

            if choice.lower() == 'd':
                target = prompt("Snapshot number to delete")
                snap = self.snapshots.get(target)
                if not snap or snap.project != project.alias:
                    show_error("Snapshot not found")
                    time.sleep(1)
                elif confirm(f"Delete snapshot #{snap.id}?", default=False):
                    self.snapshots.delete(snap.id)
                    show_success("Snapshot deleted")
                    time.sleep(0.6)
                continue

            snap = self.snapshots.get(choice)
            if not snap or snap.project != project.alias:
                show_error("Snapshot not found")
                time.sleep(1)
                continue

            if self.restore_snapshot(snap):
                return True

    def snapshots_menu(self):
        """Cross-project snapshot browser, grouped by capture batch."""
        while True:
            clear_screen()
            print(NEXUS_LOGO)
            print_header("SESSION SNAPSHOTS")

            batches = self.snapshots.batches()
            if not batches:
                print(f"\n  {C.DIM}No snapshots saved yet{C.RESET}")
                print(f"  {C.DIM}Use [K] to capture the sessions running right now{C.RESET}\n")
            else:
                for stamp, snaps in batches:
                    label = snaps[0].stamp if snaps else stamp
                    print(f"\n  {C.CYAN}▪ {label}{C.RESET} {C.DIM}({len(snaps)} window"
                          f"{'s' if len(snaps) != 1 else ''}){C.RESET}")
                    for s in sorted(snaps, key=lambda x: x.id):
                        desc = s.description or f"{C.DIM}(no description){C.RESET}"
                        print(f"    {C.MAGENTA}{s.id:>3}{C.RESET} │ {self._tool_tag(s.tool)} │ "
                              f"{C.BOLD}{s.project:<13}{C.RESET} {desc[:55]}")
                print()

            print_divider()
            print(f"  {C.YELLOW}[#]{C.RESET} Restore one   {C.YELLOW}[A]{C.RESET} Restore a whole batch   "
                  f"{C.YELLOW}[B]{C.RESET} Back")
            print_divider()

            choice = prompt("Select snapshot or option")
            if not choice or choice.lower() == 'b':
                return False

            if choice.lower() == 'a':
                if self._restore_batch_ui(batches):
                    return True
                continue

            snap = self.snapshots.get(choice)
            if not snap:
                show_error("Snapshot not found")
                time.sleep(1)
                continue

            if self.restore_snapshot(snap):
                return True

    def _restore_batch_ui(self, batches):
        """Rebuild every window of one capture batch, each in its own window."""
        if not batches:
            return False

        print()
        for i, (stamp, snaps) in enumerate(batches, 1):
            label = snaps[0].stamp if snaps else stamp
            names = ", ".join(sorted({s.project for s in snaps}))
            print(f"  {C.CYAN}{i:>2}{C.RESET} │ {label} {C.DIM}({len(snaps)}): {names}{C.RESET}")
        print()

        choice = prompt("Batch number to restore")
        if not choice.isdigit() or not (1 <= int(choice) <= len(batches)):
            show_error("Invalid batch")
            time.sleep(1)
            return False

        snaps = sorted(batches[int(choice) - 1][1], key=lambda s: s.id)
        if not confirm(f"Open {len(snaps)} tmux windows?", default=True):
            return False

        first_window = None
        restored = 0
        for snap in snaps:
            win = self.restore_snapshot(snap, new_window=True)
            if win:
                restored += 1
                first_window = first_window or win

        if not restored:
            show_error("Nothing could be restored")
            time.sleep(1.5)
            return False

        if first_window:
            subprocess.run(['tmux', 'select-window', '-t', first_window],
                           capture_output=True)
        show_success(f"{restored} window(s) restored")
        time.sleep(1)
        return True

    def capture_snapshots_ui(self):
        """Scan tmux for live agent sessions and save them as snapshots."""
        clear_screen()
        print(NEXUS_LOGO)
        print_header("CAPTURE SESSIONS")

        show_info("Scanning tmux panes for Claude/Codex sessions...")
        found = self.detector.scan()

        if not found:
            show_warning("No live Claude or Codex sessions found in tmux")
            return

        # Match each session to a configured project by path
        by_path = {normalize_path(p.path): p for p in self.manager.projects}
        for item in found:
            item['project'] = by_path.get(normalize_path(item['path']))

        print()
        for i, item in enumerate(found, 1):
            proj = item['project'].alias if item['project'] else f"{C.RED}unmatched{C.RESET}"
            print(f"  {C.CYAN}{i:>2}{C.RESET} │ {self._tool_tag(item['tool'])} │ "
                  f"{C.BOLD}{item['window']:<18}{C.RESET} {proj:<14} "
                  f"{C.DIM}{item['session_id'][:8]}…{C.RESET}")
        print()

        unmatched = [i for i in found if not i['project']]
        if unmatched:
            show_warning(f"{len(unmatched)} session(s) have no matching project "
                         f"and will be skipped")

        print_divider()
        print(f"  {C.YELLOW}[A]{C.RESET} Capture all   {C.YELLOW}[#]{C.RESET} Capture one   "
              f"{C.YELLOW}[B]{C.RESET} Back")
        print_divider()

        choice = prompt("Select")
        if not choice or choice.lower() == 'b':
            return

        if choice.lower() == 'a':
            targets = [i for i in found if i['project']]
        elif choice.isdigit() and 1 <= int(choice) <= len(found):
            item = found[int(choice) - 1]
            if not item['project']:
                show_error("That session has no matching project")
                return
            targets = [item]
        else:
            show_error("Invalid selection")
            return

        batch = datetime.now().isoformat(timespec='seconds')
        saved = 0
        for item in targets:
            print()
            print_divider()
            print(f"  {C.BOLD}{item['window']}{C.RESET} "
                  f"{C.DIM}({item['project'].alias}, {item['tool']}){C.RESET}")
            print(f"  {C.DIM}Leave empty to have Claude write it for you{C.RESET}")

            desc = prompt("Description")
            if not desc:
                show_info("Asking Claude to summarize the pane...")
                desc = draft_description(item['target'], item['config_dir'])
                if desc:
                    print(f"  {C.GREEN}→{C.RESET} {desc}")
                    if not confirm("Keep this description?", default=True):
                        desc = prompt("Description") or desc
                else:
                    show_warning("Could not generate a description")
                    desc = ""

            self.snapshots.add(Snapshot(
                id=self.snapshots.next_id(),
                project=item['project'].alias,
                tool=item['tool'],
                session_id=item['session_id'],
                description=desc,
                window_name=item['window'],
                config_dir=item['config_dir'],
                batch=batch,
            ))
            saved += 1

        show_success(f"{saved} snapshot(s) saved")

    # ─────────────────────────────────────────────────────────────────────────
    # EXTERNAL PROGRAMS UI
    # ─────────────────────────────────────────────────────────────────────────

    def programs_menu(self):
        """External programs submenu."""
        while True:
            clear_screen()
            print(NEXUS_LOGO)
            print_header("EXTERNAL PROGRAMS")

            programs = self.programs.list_programs()

            if programs:
                print()
                for i, p in enumerate(programs, 1):
                    print(f"  {C.MAGENTA}{i:>2}{C.RESET} │ {C.BOLD}{p['name']:<20}{C.RESET} {C.DIM}{p['description']}{C.RESET}")
                print()
            else:
                print(f"\n  {C.DIM}No programs found in {self.programs.apps_dir}/{C.RESET}")
                print(f"  {C.DIM}Add .py files to run them from here{C.RESET}\n")

            print_divider()
            print(f"  {C.YELLOW}[N]{C.RESET} New program   {C.YELLOW}[R]{C.RESET} Refresh   {C.YELLOW}[B]{C.RESET} Back")
            print_divider()

            choice = prompt("Select program or option")

            if choice.lower() == 'b':
                break
            elif choice.lower() == 'n':
                show_info(f"Create new .py files in: {self.programs.apps_dir}/")
                input(f"\n  {C.DIM}Press ENTER to continue...{C.RESET}")
            elif choice.lower() == 'r':
                continue
            elif choice:
                self.programs.run_program(choice)
                input(f"\n  {C.DIM}Press ENTER to continue...{C.RESET}")

    # ─────────────────────────────────────────────────────────────────────────
    # SYSTEM SCRIPTS UI
    # ─────────────────────────────────────────────────────────────────────────

    def scripts_menu(self):
        """System scripts submenu."""
        while True:
            clear_screen()
            print(NEXUS_LOGO)
            print_header("SYSTEM SCRIPTS")

            scripts = self.scripts.list_scripts()

            if scripts:
                print()
                for i, s in enumerate(scripts, 1):
                    type_color = C.GREEN if s['type'] == '.sh' else C.BLUE
                    print(f"  {C.RED}{i:>2}{C.RESET} │ {type_color}{s['type']}{C.RESET} │ {C.BOLD}{s['name']:<18}{C.RESET} {C.DIM}{s['description']}{C.RESET}")
                print()
            else:
                print(f"\n  {C.DIM}No scripts found in {self.scripts.scripts_dir}/{C.RESET}")
                print(f"  {C.DIM}Add .sh or .py files to run them from here{C.RESET}\n")

            print_divider()
            print(f"  {C.YELLOW}[N]{C.RESET} New script   {C.YELLOW}[S]{C.RESET} Run with sudo   {C.YELLOW}[B]{C.RESET} Back")
            print_divider()

            choice = prompt("Select script or option")

            if choice.lower() == 'b':
                break
            elif choice.lower() == 'n':
                show_info(f"Create scripts in: {self.scripts.scripts_dir}/")
                input(f"\n  {C.DIM}Press ENTER to continue...{C.RESET}")
            elif choice.lower() == 's':
                script_id = prompt("Script number/name to run with sudo")
                if script_id:
                    self.scripts.run_script(script_id, sudo=True)
                    input(f"\n  {C.DIM}Press ENTER to continue...{C.RESET}")
            elif choice:
                self.scripts.run_script(choice)
                input(f"\n  {C.DIM}Press ENTER to continue...{C.RESET}")

    # ─────────────────────────────────────────────────────────────────────────
    # CONFIGURATION MENU
    # ─────────────────────────────────────────────────────────────────────────

    def config_menu(self):
        """Global configuration menu."""
        while True:
            clear_screen()
            print(NEXUS_LOGO)
            print_header("CONFIGURATION")

            gc = self.manager.global_config

            print(f"""
  {C.CYAN}Layout Defaults{C.RESET}
  {C.BRIGHT_BLACK}─────────────────────────────────{C.RESET}
  {C.YELLOW}1{C.RESET} │ Right panel width    : {C.GREEN}{gc['default_h_split']}%{C.RESET}
  {C.YELLOW}2{C.RESET} │ Bottom-left height   : {C.GREEN}{gc['default_v_split']}%{C.RESET}

  {C.CYAN}Project Management{C.RESET}
  {C.BRIGHT_BLACK}─────────────────────────────────{C.RESET}
  {C.YELLOW}D{C.RESET} │ Delete a project

  {C.YELLOW}B{C.RESET} │ Back to main menu
""")

            choice = prompt("Option")

            if choice.lower() == 'b':
                break
            elif choice == '1':
                try:
                    val = int(prompt("New percentage (10-90)", str(gc['default_h_split'])))
                    if 10 <= val <= 90:
                        gc['default_h_split'] = val
                        show_success("Updated")
                except ValueError:
                    show_error("Invalid number")
            elif choice == '2':
                try:
                    val = int(prompt("New percentage (10-90)", str(gc['default_v_split'])))
                    if 10 <= val <= 90:
                        gc['default_v_split'] = val
                        show_success("Updated")
                except ValueError:
                    show_error("Invalid number")
            elif choice.lower() == 'd':
                self.delete_project_ui()
                input(f"\n  {C.DIM}Press ENTER to continue...{C.RESET}")

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN MENU
    # ─────────────────────────────────────────────────────────────────────────

    def main_menu(self):
        """Display main menu and handle navigation."""
        clear_screen()
        print(NEXUS_LOGO)

        # System status bar
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        tmux_status = f"{C.GREEN}ACTIVE{C.RESET}" if os.environ.get('TMUX') else f"{C.RED}INACTIVE{C.RESET}"
        print(f"  {C.BRIGHT_BLACK}┌─────────────────────────────────────────────────────────┐{C.RESET}")
        print(f"  {C.BRIGHT_BLACK}│{C.RESET}  {C.DIM}TMUX:{C.RESET} {tmux_status}  {C.DIM}│{C.RESET}  {C.DIM}Projects:{C.RESET} {C.CYAN}{len(self.manager.projects)}{C.RESET}  {C.DIM}│{C.RESET}  {C.DIM}Snaps:{C.RESET} {C.MAGENTA}{len(self.snapshots.snapshots)}{C.RESET}  {C.DIM}│{C.RESET}  {C.DIM}{now}{C.RESET}  {C.BRIGHT_BLACK}│{C.RESET}")
        print(f"  {C.BRIGHT_BLACK}└─────────────────────────────────────────────────────────┘{C.RESET}")

        # Quick access projects
        print_header("PROJECTS")
        has_projects = self._display_projects_list(with_snapshots=True)

        # Menu options
        print_divider('═')
        print(f"""
  {C.CYAN}Quick Actions{C.RESET}                    {C.CYAN}System Tools{C.RESET}
  {C.BRIGHT_BLACK}──────────────{C.RESET}                    {C.BRIGHT_BLACK}────────────{C.RESET}
  {C.YELLOW}[N]{C.RESET} New project                 {C.YELLOW}[P]{C.RESET} External programs
  {C.YELLOW}[E]{C.RESET} Edit project                {C.YELLOW}[S]{C.RESET} System scripts
  {C.YELLOW}[C]{C.RESET} Configuration               {C.YELLOW}[H]{C.RESET} Help

  {C.CYAN}Snapshots{C.RESET}                        {C.YELLOW}[Q]{C.RESET} Quit
  {C.BRIGHT_BLACK}──────────{C.RESET}
  {C.YELLOW}[K]{C.RESET} Capture live sessions
  {C.YELLOW}[R]{C.RESET} Restore / browse all
""")
        print_divider('═')

        if has_projects:
            print(f"  {C.DIM}Enter project number/alias to launch  ·  "
                  f"{C.RESET}{C.MAGENTA}s<n>{C.RESET}{C.DIM} for that project's snapshots{C.RESET}")
        print()
        return animated_menu_prompt("Select")

    def show_help(self):
        """Display help information."""
        clear_screen()
        print(NEXUS_LOGO)
        print_header("HELP & INFO")

        print(f"""
  {C.CYAN}NEXUS Terminal Environment Manager{C.RESET}
  {C.BRIGHT_BLACK}──────────────────────────────────────────{C.RESET}

  {C.YELLOW}PROJECT LAUNCHER{C.RESET}
    Launch tmux layouts with pre-configured panels,
    virtual environments, and commands.

    Simply enter a project number or alias from the
    main menu to launch it.

  {C.YELLOW}EXTERNAL PROGRAMS{C.RESET}  {C.DIM}(apps/ directory){C.RESET}
    Run custom Python programs. Each program should
    have a main() function as entry point.

  {C.YELLOW}SYSTEM SCRIPTS{C.RESET}  {C.DIM}(scripts/ directory){C.RESET}
    Execute shell scripts for system configuration.
    Supports .sh and .py files.

  {C.YELLOW}SESSION SNAPSHOTS{C.RESET}
    Save what you were doing so a reboot doesn't lose it.

    {C.CYAN}[K]{C.RESET} scans every tmux pane for live Claude/Codex
    sessions, matches each one to its project by path, and
    stores its session id plus a description — typed by you,
    or drafted by Claude from the pane's own scrollback.

    Each project's most recent snapshots show under it on the
    main list; type {C.MAGENTA}s<number>{C.RESET} to see that project's full
    history, or {C.CYAN}[R]{C.RESET} to browse every snapshot at once.

    Restoring builds the project's usual panel layout and
    additionally resumes the conversation in the main panel
    ({C.DIM}claudep --resume{C.RESET} / {C.DIM}codext resume{C.RESET}). From {C.CYAN}[R]{C.RESET} you can
    restore a whole capture batch, one tmux window each, to
    get your workspace back the way you left it.

  {C.YELLOW}KEYBOARD SHORTCUTS{C.RESET}
    Most menus accept both numbers and letters.
    Press 'B' to go back, 'Q' to quit.

  {C.BRIGHT_BLACK}──────────────────────────────────────────{C.RESET}
  {C.DIM}"Privacy is not about hiding something.
   It's about the right to be left alone."{C.RESET}
""")
        input(f"\n  {C.DIM}Press ENTER to continue...{C.RESET}")

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN RUN LOOP
    # ─────────────────────────────────────────────────────────────────────────

    def run(self):
        """Main application loop."""
        try:
            if self.show_boot:
                self.boot_sequence()

            while True:
                choice = self.main_menu()

                if not choice:
                    continue

                choice_lower = choice.lower()

                if choice_lower == 'q':
                    clear_screen()
                    print(f"\n  {C.CYAN}Goodbye, cypherpunk.{C.RESET}")
                    print(f"  {C.DIM}\"The Net is the great equalizer.\"{C.RESET}\n")
                    break

                elif choice_lower == 'n':
                    self.create_project_ui()
                    input(f"\n  {C.DIM}Press ENTER to continue...{C.RESET}")

                elif choice_lower == 'e':
                    self.edit_project_ui()
                    input(f"\n  {C.DIM}Press ENTER to continue...{C.RESET}")

                elif choice_lower == 'c':
                    self.config_menu()

                elif choice_lower == 'p':
                    self.programs_menu()

                elif choice_lower == 's':
                    self.scripts_menu()

                elif choice_lower == 'h':
                    self.show_help()

                elif choice_lower == 'k':
                    self.capture_snapshots_ui()
                    input(f"\n  {C.DIM}Press ENTER to continue...{C.RESET}")

                elif choice_lower == 'r':
                    if self.snapshots_menu():
                        break  # a snapshot was restored

                elif (choice_lower.startswith('s') and choice_lower[1:].strip()
                        and not self.manager.get_project(choice)):
                    # s<n> / s<alias> → that project's snapshot history.
                    # Guarded so aliases like 'safety' or 'swd' still launch.
                    project = self.manager.get_project(choice[1:].strip())
                    if project:
                        if self.project_snapshots_menu(project):
                            break  # a snapshot was restored
                    else:
                        show_error(f"Project '{choice[1:].strip()}' not found")
                        time.sleep(1)

                elif self.manager.projects:
                    # Try to launch a project
                    project = self.manager.get_project(choice)
                    if project:
                        if self.tmux.launch_layout(project):
                            break  # Exit after launching
                    else:
                        show_error(f"Project '{choice}' not found")
                        time.sleep(1)
                else:
                    show_warning("Invalid option")
                    time.sleep(0.5)

        except KeyboardInterrupt:
            print(f"\n\n  {C.YELLOW}Interrupted by user{C.RESET}\n")
            sys.exit(0)


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    """Entry point for NEXUS."""
    # Initialize private directory structure
    init_private_directory()

    app = NexusApp()

    # Check for command line arguments for quick launch
    if len(sys.argv) > 1:
        arg = sys.argv[1]

        if arg in ('--help', '-h'):
            print(f"""
{C.CYAN}NEXUS{C.RESET} - Terminal Development Environment Manager

{C.YELLOW}Usage:{C.RESET}
  nexus                    Launch interactive mode
  nexus <project>          Quick launch project by alias/number
  nexus --no-boot          Skip boot animation
  nexus --list             List all projects
  nexus --help             Show this help

{C.YELLOW}Examples:{C.RESET}
  nexus myproject          Launch 'myproject'
  nexus 1                  Launch first project
""")
            return 0

        elif arg == '--list':
            app.show_boot = False
            manager = ProjectManager(DATA_FILE)
            if manager.projects:
                print(f"\n{C.CYAN}Available Projects:{C.RESET}\n")
                for i, p in enumerate(manager.projects, 1):
                    print(f"  {i}. {p.alias}")
            else:
                print(f"\n{C.DIM}No projects configured{C.RESET}")
            return 0

        elif arg == '--no-boot':
            app.show_boot = False

        else:
            # Quick launch mode
            app.show_boot = False
            project = app.manager.get_project(arg)
            if project:
                print(f"\n  {C.CYAN}Quick launching:{C.RESET} {project.alias}")
                app.tmux.launch_layout(project)
                return 0
            else:
                show_error(f"Project '{arg}' not found")
                return 1

    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
