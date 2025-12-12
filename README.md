# NEXUS - Terminal Development Environment Manager

A cypherpunk-style terminal launcher for managing tmux project layouts, external Python programs, and system scripts.

```
    ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗
    ████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝
    ██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗
    ██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║
    ██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║
    ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝
```

## Features

- **Project Launcher**: Configure and launch tmux layouts with multiple panes, virtual environments, and custom commands
- **External Programs**: Run your own Python programs from a unified interface
- **System Scripts**: Execute shell scripts for system configuration
- **Cypherpunk UI**: Professional terminal interface with ASCII art and color-coded menus

## Requirements

- Python 3.6+
- tmux (for project layouts)
- Linux/macOS

## Installation

```bash
# Clone the repository
git clone https://github.com/nelson63db/nexus-launcher.git
cd nexus-launcher

# Make executable (optional)
chmod +x nexus-launcher.py

# Run
python3 nexus-launcher.py
```

On first run, NEXUS will automatically create the `nexus-private/` directory structure for your personal data.

## Usage

### Interactive Mode
```bash
python3 nexus-launcher.py
```

### Quick Launch
```bash
python3 nexus-launcher.py myproject    # Launch project by alias
python3 nexus-launcher.py 1            # Launch project by number
python3 nexus-launcher.py --no-boot    # Skip boot animation
python3 nexus-launcher.py --list       # List all projects
```

### Menu Options

| Key | Action |
|-----|--------|
| `1-N` | Launch project by number |
| `N` | Create new project |
| `P` | External programs menu |
| `S` | System scripts menu |
| `C` | Configuration |
| `Q` | Quit |

## Directory Structure

```
nexus-launcher/
├── nexus-launcher.py      # Main program (public)
├── README.md              # This file (public)
├── .gitignore             # Git configuration (public)
│
└── nexus-private/         # YOUR PERSONAL DATA (git-ignored)
    ├── apps/              # Your Python programs
    ├── scripts/           # Your shell scripts
    ├── projects.json      # Your project configurations
    └── README.md          # Instructions for backup
```

## Personal Data (nexus-private/)

The `nexus-private/` directory is automatically created and contains all your personal configurations. This directory is excluded from git via `.gitignore`.

### Adding Python Programs

Create `.py` files in `nexus-private/apps/`:

```python
#!/usr/bin/env python3
"""
My Custom Program
Description of what it does
"""

def main():
    print("Hello from my program!")
    return 0

if __name__ == "__main__":
    exit(main())
```

### Adding Shell Scripts

Create `.sh` files in `nexus-private/scripts/`:

```bash
#!/bin/bash
# Description: What this script does

echo "Running my script..."
# Your commands here
```

### Backing Up Personal Data

We recommend initializing a private git repository inside `nexus-private/` to backup your personal configurations:

```bash
cd nexus-private
git init
git add .
git commit -m "Initial backup"
git remote add origin git@github.com:YOUR_USER/nexus-private.git
git push -u origin main
```

This keeps your personal data versioned and backed up separately from the public NEXUS repository.

## Creating Projects

1. Run NEXUS and press `N` for new project
2. Enter:
   - **Alias**: Short name for the project
   - **Path**: Absolute path to project directory
   - **Venv Path**: Absolute path to virtual environment
3. Configure each tmux pane:
   - Commands to run
   - Whether to activate venv
   - Whether to cd to project directory

## License

MIT License - Feel free to use and modify.

---

*"In code we trust"*
