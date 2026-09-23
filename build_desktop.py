import shutil
import subprocess
import sys
import platform
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> None:
    bundle_dir = ROOT / "dist" / "JarvisApp"
    system = platform.system().lower()
    architecture = platform.machine().lower().replace("amd64", "x86_64")
    archive_path = ROOT / "dist" / f"JarvisApp-{system}-{architecture}.zip"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    if archive_path.exists():
        archive_path.unlink()

    pyinstaller = shutil.which("pyinstaller") or sys.executable
    command = [pyinstaller]
    if pyinstaller == sys.executable:
        command.append("-m")
        command.append("PyInstaller")
    command.extend(["--clean", "--noconfirm", str(ROOT / "jarvis_desktop.spec")])
    subprocess.run(command, cwd=ROOT, check=True)
    shutil.copy2(ROOT / ".env.example", bundle_dir / ".env.example")
    shutil.make_archive(str(archive_path.with_suffix("")), "zip", ROOT / "dist", "JarvisApp")
    print(f"Desktop build created in {bundle_dir}")
    print(f"Downloadable archive created at {archive_path}")


if __name__ == "__main__":
    main()