"""Freeze and stage the backend on the same OS/architecture as the desktop."""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    source = ROOT / "build/argus-backend"
    executable = source / ("argus-backend.exe" if os.name == "nt" else "argus-backend")
    if not args.prepare_only:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                        str(ROOT / "argus_backend.spec"), "--distpath", str(ROOT / "build"),
                        "--workpath", str(ROOT / "build/.work")], cwd=REPO, check=True)
        for arguments in (["--verify-frozen-runtime"],
                          ["-I", "-m", "argus_skill.tools.manager_live_view", "--help"],
                          ["-c", "import argus_skill.trial.desktop, certifi; print('desktop-trial-ready')"]):
            subprocess.run([str(executable), *arguments], cwd=REPO, check=True)
        if os.environ.get("ARGUS_TEST_NATIVE_COPILOT") == "1":
            with tempfile.TemporaryDirectory(prefix="argus-frozen-native-") as temporary:
                subprocess.run(
                    [str(executable), "-c", "from argus_skill.trial.native_cli import install_native_copilot; install_native_copilot(); print('native-copilot-ready')"],
                    cwd=temporary, check=True,
                    env={**os.environ, "ARGUS_SKILL_HOME": temporary},
                )
    if not executable.is_file():
        raise SystemExit("Frozen backend missing. Run npm run build:backend first.")
    destination = ROOT / "resources/argus-backend"
    if destination.exists():
        for child in destination.iterdir():
            if child.name != ".gitkeep":
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True)
    print(f"Desktop backend staged: {destination}")


if __name__ == "__main__":
    main()
