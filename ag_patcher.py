import os
import re
import shutil
import argparse


TARGET_FILES = [
    ("resources/app/out/vs/workbench/api/node/extensionHostProcess.js", "extensionHostProcess"),
    ("resources/app/out/vs/workbench/api/worker/extensionHostWorkerMain.js", "extensionHostWorkerMain"),
    ("resources/app/out/main.js", "main"),
    ("resources/app/out/vs/code/node/cliProcessMain.js", "cliProcessMain")
]


def apply_patches(d, target_url="http://127.0.0.1:9099"):
    print(f"Applying patches pointing to {target_url}...")
    url_pattern = re.compile(r"https://([a-zA-Z0-9.\-]*cloudcode[a-zA-Z0-9.\-]*\.googleapis\.com|127\.0\.0\.1:\d+)")
    inject_code = "process.env.NODE_TLS_REJECT_UNAUTHORIZED='0';"
    
    patched_count = 0
    for rel_path, name in TARGET_FILES:
        full_path = os.path.join(d, rel_path)
        if not os.path.exists(full_path):
            print(f"File not found: {full_path}, skipping.")
            continue
            
        bak_path = full_path + ".bak"
        if not os.path.exists(bak_path):
            shutil.copy2(full_path, bak_path)
            print(f"Created backup: {bak_path}")
            
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        new_content = url_pattern.sub(target_url, content)
        if "NODE_TLS_REJECT_UNAUTHORIZED" not in new_content:
            new_content = inject_code + new_content
            
        if new_content != content:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"Successfully patched {name}")
            patched_count += 1
        else:
            print(f"{name} is already patched or up-to-date")
            
    print(f"Patched {patched_count} files successfully.")


def restore_patches(d):
    print("Restoring original files from backups...")
    restored_count = 0
    for rel_path, name in TARGET_FILES:
        full_path = os.path.join(d, rel_path)
        bak_path = full_path + ".bak"
        if os.path.exists(bak_path):
            shutil.copy2(bak_path, full_path)
            os.remove(bak_path)
            print(f"Restored {name} from backup")
            restored_count += 1
        else:
            print(f"No backup found for {name}")
    print(f"Restored {restored_count} files successfully.")


parser = argparse.ArgumentParser(
    description="Antigravity IDE patch tool. Developed with <3 by dijey099"
)

action = parser.add_mutually_exclusive_group(required=True)
action.add_argument(
    "-p", "--patch",
    action="store_true",
    help="Patch AG IDE"
)
action.add_argument(
    "-u", "--unpatch",
    action="store_true",
    help="Restore AG IDE"
)

parser.add_argument(
    "directory",
    nargs="?",
    help="Path to the AG IDE directory"
)

parser.add_argument(
    "--url",
    type=str,
    default="http://localhost:9099",
    help="AG Proxy URL. Default to http://localhost:9099 if not specified"
)

args = parser.parse_args()

if args.patch:
    if args.directory is None:
        parser.error("--patch requires the 'directory' argument.")
    else:
        if os.path.isdir(args.directory):
            apply_patches(args.directory, args.url)
        else:
            print(f"The specified path is not a directory: {args.directory}")

elif args.unpatch:
    if args.directory is None:
        parser.error("--patch requires the 'directory' argument.")
    else:
        if os.path.isdir(args.directory):
            restore_patches(args.directory)
        else:
            print(f"The specified path is not a directory: {args.directory}")

else:
    print("Would you like to patch or unpatch ?")
