# [AI_INFO] This module defines automation tasks using the 'invoke' library.
# [AI_FLOW] Root: Project Directory. Input: Dry-run flag (boolean). Output: Cleaned directory tree.
# [AI_SIDE_EFFECT] Deletes files and directories matching specific cache patterns.
# [AI_CONSTRAINT] Uses Path.glob() to recursively find patterns. Skips '.venv' directory to preserve environment integrity.

import shutil
from pathlib import Path
from invoke import task
from rich.console import Console

console = Console()

# [AI_TASK_DEFINITION] 'mrproper' is a cleanup task for development caches.
# [AI_PARAMETERS] c: Context (standard for invoke), dry: Boolean to simulate deletion.
# [AI_PATTERNS_TARGETED] Targets Python bytecode, test caches, and type-checker caches.
@task(help={"dry": "If True, only lists the files/folders that would be removed without deleting them."})
def mrproper(c, dry=False):
    """
    Clean up all the pycache and other cache files in the project.
    
    This includes:
    - __pycache__ directories
    - .pytest_cache directories
    - .mypy_cache directories
    - .ruff_cache directories
    - *.pyc, *.pyo, *.pyd files
    """
    # [AI_UI_FEEDBACK] Uses 'rich' library for formatted console output.
    console.print("[bold blue]Cleaning up project caches...[/bold blue]")
    
    # [AI_TARGET_LIST] List of glob patterns for temporary/cached build artifacts.
    patterns = [
        "**/__pycache__",
        "**/.pytest_cache",
        "**/.mypy_cache",
        "**/.ruff_cache",
        "**/*.pyc",
        "**/*.pyo",
        "**/*.pyd",
    ]
    
    count = 0
    root = Path(".")
    
    # [AI_ITERATION_LOGIC] Recursively glob patterns from the project root.
    for pattern in patterns:
        for path in root.glob(pattern):
            # [AI_SAFETY_CHECK] Critical: Do not delete anything inside .venv.
            # Skip .venv directory to avoid accidental deletion of environment files
            if ".venv" in path.parts:
                continue
                
            if dry:
                # [AI_DRY_RUN_PATH] Logs intention without modification.
                console.print(f"[yellow]Would remove:[/yellow] {path}")
            else:
                try:
                    # [AI_DELETION_LOGIC] Handles both directories and individual files.
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    console.print(f"[red]Removed:[/red] {path}")
                    count += 1
                except Exception as e:
                    # [AI_ERROR_TRAP] Catches permission errors or OS-level file access issues.
                    console.print(f"[bold red]Error removing {path}:[/bold red] {e}")

    # [AI_FINAL_STATE] Reports the number of items removed or completion status.
    if dry:
        console.print("[bold green]Dry run completed.[/bold green]")
    else:
        console.print(f"[bold green]Cleanup completed! [cyan]{count}[/cyan] items removed.[/bold green]")
