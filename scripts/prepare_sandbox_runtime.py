"""Prepare a private development interpreter; installed builds use the frozen worker."""
from pathlib import Path
import shutil
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    target = root/'.dev-deps/sandbox-python'
    target.mkdir(parents=True, exist_ok=True)
    base = Path(sys.base_prefix)
    for name in ('python.exe','python3.dll','python314.dll','vcruntime140.dll','vcruntime140_1.dll','LICENSE.txt'):
        if (base/name).is_file():
            shutil.copy2(base/name, target/name)
    for name in ('Lib','DLLs'):
        shutil.copytree(base/name, target/name, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns('site-packages','__pycache__','test','tests','idlelib','tkinter'))
    site = target/'Lib/site-packages'
    site.mkdir(exist_ok=True)
    paths = [root/'.venv-build/Lib/site-packages', root/'src']
    (site/'ape_development.pth').write_text(''.join(str(p.resolve())+'\n' for p in paths),encoding='utf-8')
    print(target)


if __name__ == '__main__':
    main()
