import os
import sys
import subprocess
from pathlib import Path

def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "install"
    startup_dir = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    target_vbs = startup_dir / "AuraBeatAddonServer.vbs"
    server_dir = Path(__file__).parent.resolve()

    if action == "uninstall":
        if target_vbs.exists():
            target_vbs.unlink()
            print("[SUCCESS] Removed AuraBeat Addon Server from Windows startup.")
        else:
            print("[INFO] Server was not configured in Windows startup.")
        return

    # Install using explicit --app-dir
    vbs_content = (
        'Set WshShell = CreateObject("WScript.Shell")\r\n'
        f'WshShell.Run "cmd /c python -m uvicorn main:app --app-dir ""{server_dir}"" --port 3000 --host 0.0.0.0", 0, False\r\n'
    )

    with open(target_vbs, "w", encoding="utf-8") as f:
        f.write(vbs_content)

    print("=" * 60)
    print(" [SUCCESS] AuraBeat Server is now set to ALWAYS RUN!")
    print("=" * 60)
    print(f" Startup file: {target_vbs}")
    print(" - Will launch automatically every time your PC boots/logs in.")
    print(" - Runs silently in the background (no open terminal window).")
    print(" - Addon URL: http://192.168.1.13:3000/manifest.json")
    print("-" * 60)

if __name__ == "__main__":
    main()
